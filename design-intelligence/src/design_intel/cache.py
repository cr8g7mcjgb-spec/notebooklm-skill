"""Content-addressed cache for analysis results and embedding vectors.

The cache key is (content hash, producer, producer version). Re-running the
indexer over an unchanged library is therefore free, and changing the embedder
invalidates only the vectors — the expensive-to-recompute analysis survives.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import numpy as np

CHUNK = 1 << 20


def file_hash(path: str | Path) -> str:
    """Hash file bytes plus size — fast, and stable across copies/moves."""
    digest = hashlib.blake2b(digest_size=16)
    size = os.path.getsize(path)
    digest.update(str(size).encode())
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def stable_id(path: str | Path, root: str | Path | None = None) -> str:
    """A reference id that survives re-indexing: hash of the relative path."""
    p = Path(path).resolve()
    try:
        key = str(p.relative_to(Path(root).resolve())) if root else str(p)
    except ValueError:
        key = str(p)
    return hashlib.blake2b(key.encode("utf-8"), digest_size=10).hexdigest()


class Cache:
    """Two-tier disk cache: JSON blobs and .npy vectors."""

    def __init__(self, path: str | Path = ".design-cache", enabled: bool = True) -> None:
        self.root = Path(path)
        self.enabled = enabled
        if enabled:
            (self.root / "analysis").mkdir(parents=True, exist_ok=True)
            (self.root / "vectors").mkdir(parents=True, exist_ok=True)

    def _slot(self, kind: str, key: str, suffix: str) -> Path:
        # shard by the first two hex chars so directories stay browsable
        return self.root / kind / key[:2] / f"{key}{suffix}"

    @staticmethod
    def key(*parts: Any) -> str:
        raw = "|".join(str(p) for p in parts)
        return hashlib.blake2b(raw.encode("utf-8"), digest_size=16).hexdigest()

    # -- json ------------------------------------------------------------

    def get_json(self, key: str) -> Any | None:
        if not self.enabled:
            return None
        slot = self._slot("analysis", key, ".json")
        if not slot.exists():
            return None
        try:
            return json.loads(slot.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def set_json(self, key: str, value: Any) -> None:
        if not self.enabled:
            return
        slot = self._slot("analysis", key, ".json")
        slot.parent.mkdir(parents=True, exist_ok=True)
        tmp = slot.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        tmp.replace(slot)

    # -- vectors ---------------------------------------------------------

    def get_vector(self, key: str) -> np.ndarray | None:
        if not self.enabled:
            return None
        slot = self._slot("vectors", key, ".npy")
        if not slot.exists():
            return None
        try:
            return np.load(slot).astype(np.float32)
        except (ValueError, OSError):
            return None

    def set_vector(self, key: str, vector: np.ndarray) -> None:
        if not self.enabled:
            return
        slot = self._slot("vectors", key, ".npy")
        slot.parent.mkdir(parents=True, exist_ok=True)
        tmp = slot.with_suffix(".tmp.npy")  # np.save appends .npy to any other suffix
        np.save(tmp, np.asarray(vector, dtype=np.float32))
        tmp.replace(slot)

    # -- maintenance -----------------------------------------------------

    def clear(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)
        if self.enabled:
            (self.root / "analysis").mkdir(parents=True, exist_ok=True)
            (self.root / "vectors").mkdir(parents=True, exist_ok=True)

    def stats(self) -> dict[str, int]:
        def measure(kind: str) -> tuple[int, int]:
            files = list((self.root / kind).rglob("*")) if self.root.exists() else []
            files = [f for f in files if f.is_file()]
            return len(files), sum(f.stat().st_size for f in files)

        a_count, a_bytes = measure("analysis")
        v_count, v_bytes = measure("vectors")
        return {
            "analysis_entries": a_count,
            "vector_entries": v_count,
            "bytes": a_bytes + v_bytes,
        }
