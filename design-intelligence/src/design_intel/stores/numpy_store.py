"""Dependency-free store: an .npy matrix plus a JSONL sidecar.

Exact brute-force cosine search. On a laptop this is instant up to ~100k
references, which covers most personal design libraries — swap to LanceDB or
Qdrant past that. It exists so `pip install design-intel` with no extras still
gives a fully working system.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from ..registry import register_store
from ..serde import reference_from_dict
from ..types import Reference
from .base import BaseStore, rank


@register_store("numpy")
class NumpyStore(BaseStore):
    name = "numpy"

    def __init__(self, path: str = ".design-index", **kwargs: Any) -> None:
        super().__init__(path=path, **kwargs)
        self.dir = Path(path)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.vectors_path = self.dir / "vectors.npy"
        self.records_path = self.dir / "records.jsonl"
        self._lock = threading.Lock()
        self._refs: list[Reference] = []
        self._vectors: np.ndarray | None = None
        self._index: dict[str, int] = {}
        self._load()

    # -- persistence -----------------------------------------------------

    def _load(self) -> None:
        if self.records_path.exists():
            with self.records_path.open(encoding="utf-8") as fh:
                self._refs = [reference_from_dict(json.loads(line)) for line in fh if line.strip()]
            self._index = {ref.id: i for i, ref in enumerate(self._refs)}
        if self.vectors_path.exists():
            vectors = np.load(self.vectors_path)
            if len(vectors) == len(self._refs):
                self._vectors = vectors.astype(np.float32)
            else:  # sidecar drift — rebuild on next upsert rather than mis-rank
                self._vectors = None
                self._refs = []
                self._index = {}

    def _flush(self) -> None:
        tmp_records = self.records_path.with_suffix(".jsonl.tmp")
        with tmp_records.open("w", encoding="utf-8") as fh:
            for ref in self._refs:
                fh.write(json.dumps(ref.to_dict(), ensure_ascii=False) + "\n")
        tmp_records.replace(self.records_path)
        if self._vectors is not None:
            # must keep the .npy suffix: np.save appends it otherwise
            tmp_vectors = self.vectors_path.with_suffix(".tmp.npy")
            np.save(tmp_vectors, self._vectors)
            tmp_vectors.replace(self.vectors_path)

    # -- interface -------------------------------------------------------

    def upsert(self, references: Sequence[Reference], vectors: np.ndarray) -> None:
        vectors = np.asarray(vectors, dtype=np.float32)
        if vectors.ndim == 1:
            vectors = vectors[None, :]
        if len(references) != len(vectors):
            raise ValueError("references and vectors must be the same length")

        with self._lock:
            for ref, vec in zip(references, vectors, strict=True):
                existing = self._index.get(ref.id)
                if existing is not None:
                    self._refs[existing] = ref
                    self._vectors[existing] = vec  # type: ignore[index]
                    continue
                if self._vectors is None:
                    self._vectors = vec[None, :].copy()
                else:
                    if self._vectors.shape[1] != vec.shape[0]:
                        raise ValueError(
                            f"vector dim {vec.shape[0]} does not match index dim "
                            f"{self._vectors.shape[1]} — reindex after changing embedder"
                        )
                    self._vectors = np.vstack([self._vectors, vec[None, :]])
                self._index[ref.id] = len(self._refs)
                self._refs.append(ref)
            self._flush()

    def search(
        self, vector: np.ndarray, limit: int = 10, filters: dict[str, Any] | None = None
    ) -> list[tuple[Reference, float]]:
        if self._vectors is None or not len(self._refs):
            return []
        query = np.asarray(vector, dtype=np.float32).ravel()
        query = query / max(float(np.linalg.norm(query)), 1e-8)
        return rank(self._vectors @ query, self._refs, limit, filters)

    def get(self, ref_id: str) -> Reference | None:
        idx = self._index.get(ref_id)
        return self._refs[idx] if idx is not None else None

    def get_vector(self, ref_id: str) -> np.ndarray | None:
        idx = self._index.get(ref_id)
        if idx is None or self._vectors is None:
            return None
        return self._vectors[idx].copy()

    def delete(self, ref_ids: Sequence[str]) -> int:
        with self._lock:
            drop = {self._index[r] for r in ref_ids if r in self._index}
            if not drop:
                return 0
            keep = [i for i in range(len(self._refs)) if i not in drop]
            self._refs = [self._refs[i] for i in keep]
            if self._vectors is not None:
                self._vectors = self._vectors[keep] if keep else None
            self._index = {ref.id: i for i, ref in enumerate(self._refs)}
            self._flush()
            return len(drop)

    def iter_references(self, collection: str | None = None) -> Iterable[Reference]:
        for ref in self._refs:
            if collection is None or ref.collection == collection:
                yield ref

    def count(self, collection: str | None = None) -> int:
        return sum(1 for _ in self.iter_references(collection))
