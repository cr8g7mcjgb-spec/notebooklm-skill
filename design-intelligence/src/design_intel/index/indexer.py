"""Incremental indexing.

Two independent caches keyed by content hash mean the common case — "I dropped
6 new posters into the folder" — only pays for those 6. Changing the embedder
reuses the analysis; changing the analyzer version reuses the vectors.

Files are matched by path, so a moved file is re-indexed under a new id while
the old record is pruned (with `prune=True`). Content-identical duplicates share
their cached analysis and vector, so duplicates cost lookup time only.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..analysis.pipeline import ANALYZER_VERSION, analyze_image
from ..cache import Cache, file_hash, stable_id
from ..serde import features_from_dict
from ..stores.base import BaseStore
from ..types import Reference

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".avif"}
EMBED_BATCH = 8


@dataclass
class IndexReport:
    indexed: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    pruned: int = 0
    elapsed: float = 0.0
    errors: list[dict[str, str]] = field(default_factory=list)
    collection: str = "default"

    def to_dict(self) -> dict[str, Any]:
        return {
            "indexed": self.indexed,
            "updated": self.updated,
            "skipped": self.skipped,
            "failed": self.failed,
            "pruned": self.pruned,
            "elapsed_seconds": round(self.elapsed, 2),
            "errors": self.errors[:10],
            "collection": self.collection,
        }


def discover(paths: Sequence[str | Path], recursive: bool = True) -> list[Path]:
    found: list[Path] = []
    for raw in paths:
        path = Path(raw).expanduser()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
            found.append(path.resolve())
        elif path.is_dir():
            walker = path.rglob("*") if recursive else path.glob("*")
            found.extend(
                p.resolve() for p in walker if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
            )
    return sorted(set(found))


class Indexer:
    def __init__(
        self,
        store: BaseStore,
        embedder: Any,
        cache: Cache,
        max_workers: int = 4,
        collection: str = "default",
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.cache = cache
        self.max_workers = max(1, max_workers)
        self.collection = collection

    def index(
        self,
        paths: Sequence[str | Path],
        collection: str | None = None,
        tags: Sequence[str] | None = None,
        metadata: dict[str, Any] | None = None,
        recursive: bool = True,
        force: bool = False,
        prune: bool = False,
        root: str | Path | None = None,
        progress: Callable[[str, int, int], None] | None = None,
    ) -> IndexReport:
        started = time.time()
        collection = collection or self.collection
        report = IndexReport(collection=collection)

        files = discover(paths, recursive=recursive)
        known = {} if force else self.store.hashes(collection)
        seen_paths: set[str] = set()

        pending: list[tuple[Path, str, str]] = []  # (path, ref_id, content_hash)
        for path in files:
            key = str(path)
            seen_paths.add(key)
            try:
                digest = file_hash(path)
            except OSError as exc:
                report.failed += 1
                report.errors.append({"path": key, "error": str(exc)})
                continue
            if not force and known.get(key) == digest:
                report.skipped += 1
                continue
            pending.append((path, stable_id(path, root), digest))

        total = len(pending)
        for start in range(0, total, EMBED_BATCH):
            batch = pending[start : start + EMBED_BATCH]
            refs, vectors = self._process_batch(batch, collection, tags, metadata, report)
            if refs:
                self.store.upsert(refs, np.stack(vectors))
            if progress:
                progress("indexing", min(start + len(batch), total), total)

        if prune:
            report.pruned = self._prune(seen_paths, collection)

        report.elapsed = time.time() - started
        return report

    # -- internals -------------------------------------------------------

    def _process_batch(
        self,
        batch: Sequence[tuple[Path, str, str]],
        collection: str,
        tags: Sequence[str] | None,
        metadata: dict[str, Any] | None,
        report: IndexReport,
    ) -> tuple[list[Reference], list[np.ndarray]]:
        # 1. vectors first — analysis may want them for zero-shot style scoring
        vectors: dict[str, np.ndarray] = {}
        to_embed: list[tuple[Path, str]] = []
        for path, _, digest in batch:
            key = Cache.key("vec", digest, self.embedder.name, self.embedder.dim)
            cached = self.cache.get_vector(key)
            if cached is not None:
                vectors[digest] = cached
            else:
                to_embed.append((path, digest))

        if to_embed:
            try:
                fresh = self.embedder.embed_images([p for p, _ in to_embed])
                for (_path, digest), vector in zip(to_embed, fresh, strict=True):
                    vectors[digest] = vector
                    self.cache.set_vector(
                        Cache.key("vec", digest, self.embedder.name, self.embedder.dim), vector
                    )
                self._cache_patches(to_embed)
            except Exception as exc:
                for path, _ in to_embed:
                    report.failed += 1
                    report.errors.append({"path": str(path), "error": f"embed failed: {exc}"})

        # 2. analysis, parallel across the batch
        def analyze(item: tuple[Path, str, str]) -> tuple[str, Any]:
            path, _, digest = item
            key = Cache.key("analysis", digest, ANALYZER_VERSION, self.embedder.name)
            cached = self.cache.get_json(key)
            if cached is not None:
                return digest, features_from_dict(cached)
            features = analyze_image(
                path, embedder=self.embedder, image_vector=vectors.get(digest)
            )
            self.cache.set_json(key, features.to_dict())
            return digest, features

        analyzed: dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {pool.submit(analyze, item): item for item in batch if item[2] in vectors}
            for future, item in futures.items():
                try:
                    digest, features = future.result()
                    analyzed[digest] = features
                except Exception as exc:
                    report.failed += 1
                    report.errors.append({"path": str(item[0]), "error": f"analysis failed: {exc}"})

        # 3. assemble records
        refs: list[Reference] = []
        out_vectors: list[np.ndarray] = []
        now = time.time()
        for path, ref_id, digest in batch:
            if digest not in analyzed or digest not in vectors:
                continue
            existing = self.store.get(ref_id)
            refs.append(
                Reference(
                    id=ref_id,
                    path=str(path),
                    content_hash=digest,
                    features=analyzed[digest],
                    metadata={**(existing.metadata if existing else {}), **(metadata or {})},
                    tags=sorted(set((existing.tags if existing else []) + list(tags or []))),
                    caption=existing.caption if existing else "",
                    collection=collection,
                    indexed_at=now,
                    embedder=self.embedder.name,
                )
            )
            out_vectors.append(vectors[digest])
            if existing:
                report.updated += 1
            else:
                report.indexed += 1
        return refs, out_vectors

    def _cache_patches(self, items: Sequence[tuple[Path, str]]) -> None:
        """Persist patch grids for late-interaction reranking, when available."""
        if not getattr(self.embedder, "multi_vector", False):
            return
        try:
            grids = self.embedder.embed_patches([p for p, _ in items])
        except Exception:
            return
        for (_, digest), grid in zip(items, grids, strict=False):
            self.cache.set_vector(Cache.key("patches", digest, self.embedder.name), grid)

    def _prune(self, seen: set[str], collection: str) -> int:
        stale = [
            ref.id
            for ref in self.store.iter_references(collection)
            if ref.path not in seen and not Path(ref.path).exists()
        ]
        return self.store.delete(stale) if stale else 0
