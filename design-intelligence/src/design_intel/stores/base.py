"""Vector store interface plus the shared metadata-filter engine.

Filters are evaluated in Python against the stored `Reference`, which keeps
behaviour identical across every backend. Backends that can push a filter down
(Qdrant payload filters, LanceDB SQL predicates) do so for `collection` — the
one field big enough to matter — and over-fetch for the rest.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from typing import Any

import numpy as np

from ..style.classifier import resolve
from ..types import Reference

OVERFETCH = 6  # multiplier when a filter has to be applied after the ANN search


class BaseStore(ABC):
    name: str = "base"

    def __init__(self, **kwargs: Any) -> None:
        self.options = kwargs

    @abstractmethod
    def upsert(self, references: Sequence[Reference], vectors: np.ndarray) -> None: ...

    @abstractmethod
    def search(
        self, vector: np.ndarray, limit: int = 10, filters: dict[str, Any] | None = None
    ) -> list[tuple[Reference, float]]: ...

    @abstractmethod
    def get(self, ref_id: str) -> Reference | None: ...

    @abstractmethod
    def delete(self, ref_ids: Sequence[str]) -> int: ...

    @abstractmethod
    def iter_references(self, collection: str | None = None) -> Iterable[Reference]: ...

    @abstractmethod
    def count(self, collection: str | None = None) -> int: ...

    def get_vector(self, ref_id: str) -> np.ndarray | None:
        raise NotImplementedError

    def hashes(self, collection: str | None = None) -> dict[str, str]:
        """path -> content_hash, used by the indexer to skip unchanged files."""
        return {ref.path: ref.content_hash for ref in self.iter_references(collection)}

    def collections(self) -> list[str]:
        return sorted({ref.collection for ref in self.iter_references()})

    def close(self) -> None:
        return None

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "count": self.count(), "collections": self.collections()}


def matches(ref: Reference, filters: dict[str, Any] | None) -> bool:
    """Evaluate a filter spec against one reference.

    Supported keys:
      collection : str | list[str]
      tags       : list[str]            -- any-match
      all_tags   : list[str]            -- all-match
      styles     : list[str]            -- primary style or a top-3 style
      min_style_confidence : float
      metadata   : dict                 -- exact match on metadata fields
      where      : {feature.path: value | [min, max] | list[str]}
      exclude_ids: list[str]
    """
    if not filters:
        return True

    collection = filters.get("collection")
    if collection:
        wanted = {collection} if isinstance(collection, str) else set(collection)
        if ref.collection not in wanted:
            return False

    if filters.get("exclude_ids") and ref.id in set(filters["exclude_ids"]):
        return False

    tags = filters.get("tags")
    if tags and not (set(t.lower() for t in tags) & set(t.lower() for t in ref.tags)):
        return False

    all_tags = filters.get("all_tags")
    if all_tags and not set(t.lower() for t in all_tags) <= set(t.lower() for t in ref.tags):
        return False

    styles = filters.get("styles")
    if styles:
        wanted = {s.lower() for s in ([styles] if isinstance(styles, str) else styles)}
        present = {ref.features.style.primary} | {s.style for s in ref.features.style.scores[:3]}
        if not wanted & present:
            return False

    threshold = filters.get("min_style_confidence")
    if threshold is not None and ref.features.style.confidence < float(threshold):
        return False

    for key, expected in (filters.get("metadata") or {}).items():
        if ref.metadata.get(key) != expected:
            return False

    for path, expected in (filters.get("where") or {}).items():
        value = resolve(ref.features, path)
        if value is None:
            return False
        if isinstance(expected, (list, tuple)) and len(expected) == 2 and _numeric(expected):
            lo, hi = expected
            if lo is not None and float(value) < float(lo):
                return False
            if hi is not None and float(value) > float(hi):
                return False
        elif isinstance(expected, (list, tuple, set)):
            if str(value) not in {str(e) for e in expected}:
                return False
        elif isinstance(expected, (int, float)) and not isinstance(expected, bool):
            if abs(float(value) - float(expected)) > 1e-6:
                return False
        elif str(value) != str(expected):
            return False

    return True


def _numeric(pair: Sequence[Any]) -> bool:
    return all(p is None or isinstance(p, (int, float)) and not isinstance(p, bool) for p in pair)


def rank(scores: np.ndarray, refs: Sequence[Reference], limit: int, filters: dict[str, Any] | None):
    """Sort, filter and truncate — the tail every backend shares."""
    order = np.argsort(-scores)
    out: list[tuple[Reference, float]] = []
    for idx in order:
        ref = refs[int(idx)]
        if not matches(ref, filters):
            continue
        out.append((ref, float(scores[int(idx)])))
        if len(out) >= limit:
            break
    return out
