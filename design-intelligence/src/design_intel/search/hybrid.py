"""Hybrid retrieval: image vectors + text vectors + lexical + style targeting.

Signals are combined with Reciprocal Rank Fusion rather than by adding raw
scores. RRF is the right call here because the signals live on incompatible
scales — a cosine of 0.31 from CLIP and a lexical overlap of 2.0 mean nothing to
each other, but their *ranks* do. It also degrades gracefully: with the
dependency-free embedder there is no text tower, and the lexical + style
channels simply carry the query on their own.

When the embedder is a late-interaction model (ColPali/ColQwen2), the fused
shortlist is reranked with MaxSim over cached patch grids — the two-stage
pattern from the ViDoRe work.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from ..cache import Cache
from ..embedders.base import maxsim
from ..memory.taste import TasteMemory
from ..stores.base import BaseStore, matches
from ..style.classifier import score_style
from ..style.taxonomy import get_style
from ..types import Reference, SearchHit

CANDIDATE_FACTOR = 5
MIN_CANDIDATES = 40
RERANK_DEPTH = 25
TOKEN_RE = re.compile(r"[a-z0-9\-]+")


class HybridSearcher:
    def __init__(
        self,
        store: BaseStore,
        embedder: Any,
        config: Any,
        memory: TasteMemory | None = None,
        cache: Cache | None = None,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.config = config
        self.memory = memory
        self.cache = cache

    # -- public ----------------------------------------------------------

    def search(
        self,
        text: str | None = None,
        image: str | Path | None = None,
        like_id: str | None = None,
        style: str | None = None,
        filters: dict[str, Any] | None = None,
        limit: int = 8,
        use_taste: bool = True,
    ) -> list[SearchHit]:
        if not any([text, image, like_id, style, filters]):
            raise ValueError("give at least one of: text, image, like_id, style, filters")

        depth = max(MIN_CANDIDATES, limit * CANDIDATE_FACTOR)
        channels: dict[str, list[tuple[str, float]]] = {}
        pool: dict[str, Reference] = {}
        query_vector: np.ndarray | None = None

        if image is not None or like_id is not None:
            query_vector = self._query_vector(image=image, like_id=like_id)
            if query_vector is not None:
                channels["image"] = self._vector_channel(query_vector, depth, filters, pool, like_id)

        if text and getattr(self.embedder, "supports_text", False):
            text_vector = self.embedder.embed_text([text])[0]
            channels["text"] = self._vector_channel(text_vector, depth, filters, pool, like_id)
            if query_vector is None:
                query_vector = np.asarray(text_vector, dtype=np.float32)

        if text:
            channels["lexical"] = self._lexical_channel(text, depth, filters, pool, like_id)

        if style:
            channels["style"] = self._style_channel(style, depth, filters, pool, like_id)

        if not channels:
            # nothing but metadata filters — return the filtered set, newest first
            refs = [r for r in self.store.iter_references() if matches(r, filters) and r.id != like_id]
            refs.sort(key=lambda r: -r.indexed_at)
            return [
                SearchHit(
                    reference=r, score=1.0, rank=i + 1, signals={"filter": 1.0}, why=["metadata filter"]
                )
                for i, r in enumerate(refs[:limit])
            ]

        fused = self._fuse(channels)
        hits = self._to_hits(fused, pool, channels, query_vector, use_taste, limit)

        if getattr(self.embedder, "multi_vector", False) and text:
            hits = self._maxsim_rerank(text, hits)

        for i, hit in enumerate(hits):
            hit.rank = i + 1
        return hits[:limit]

    # -- channels --------------------------------------------------------

    def _query_vector(self, image: str | Path | None, like_id: str | None) -> np.ndarray | None:
        if like_id:
            vector = self.store.get_vector(like_id)
            if vector is not None:
                return vector
            ref = self.store.get(like_id)
            if ref is None:
                raise KeyError(f"unknown reference id: {like_id}")
            image = ref.path
        if image is None:
            return None
        if self.cache is not None:
            key = Cache.key("query", str(image), self.embedder.name)
            cached = self.cache.get_vector(key)
            if cached is not None:
                return cached
            vector = self.embedder.embed_images([image])[0]
            self.cache.set_vector(key, vector)
            return vector
        return self.embedder.embed_images([image])[0]

    def _vector_channel(
        self,
        vector: np.ndarray,
        depth: int,
        filters: dict[str, Any] | None,
        pool: dict[str, Reference],
        exclude: str | None,
    ) -> list[tuple[str, float]]:
        out: list[tuple[str, float]] = []
        for ref, score in self.store.search(vector, limit=depth, filters=filters):
            if ref.id == exclude:
                continue
            pool[ref.id] = ref
            out.append((ref.id, score))
        return out

    def _lexical_channel(
        self,
        text: str,
        depth: int,
        filters: dict[str, Any] | None,
        pool: dict[str, Reference],
        exclude: str | None,
    ) -> list[tuple[str, float]]:
        terms = set(TOKEN_RE.findall(text.lower()))
        if not terms:
            return []
        scored: list[tuple[str, float]] = []
        for ref in self.store.iter_references():
            if ref.id == exclude or not matches(ref, filters):
                continue
            haystack = " ".join(
                [
                    ref.caption,
                    " ".join(ref.tags),
                    Path(ref.path).stem.replace("_", " ").replace("-", " "),
                    ref.features.style.primary,
                    " ".join(ref.features.style.descriptors),
                    " ".join(str(v) for v in ref.metadata.values()),
                ]
            ).lower()
            tokens = set(TOKEN_RE.findall(haystack))
            if not tokens:
                continue
            overlap = terms & tokens
            if not overlap:
                continue
            score = len(overlap) / len(terms) + 0.1 * len(overlap)
            pool[ref.id] = ref
            scored.append((ref.id, score))
        scored.sort(key=lambda kv: -kv[1])
        return scored[:depth]

    def _style_channel(
        self,
        style: str,
        depth: int,
        filters: dict[str, Any] | None,
        pool: dict[str, Reference],
        exclude: str | None,
    ) -> list[tuple[str, float]]:
        target = get_style(style)
        scored: list[tuple[str, float]] = []
        for ref in self.store.iter_references():
            if ref.id == exclude or not matches(ref, filters):
                continue
            if target is None:
                # unknown style name: fall back to whatever the classifier stored
                score = next(
                    (s.score for s in ref.features.style.scores if style.lower() in s.style), 0.0
                )
            else:
                score, _ = score_style(ref.features, target)
            if score <= 0.2:
                continue
            pool[ref.id] = ref
            scored.append((ref.id, float(score)))
        scored.sort(key=lambda kv: -kv[1])
        return scored[:depth]

    # -- fusion ----------------------------------------------------------

    def _weight(self, channel: str) -> float:
        return {
            "image": self.config.weight_image,
            "text": self.config.weight_text,
            "lexical": self.config.weight_text * 0.6,
            "style": self.config.weight_style,
        }.get(channel, 0.5)

    def _fuse(
        self, channels: dict[str, list[tuple[str, float]]]
    ) -> list[tuple[str, float, dict[str, float]]]:
        k = self.config.rrf_k
        totals: dict[str, float] = {}
        signals: dict[str, dict[str, float]] = {}
        for channel, ranked in channels.items():
            weight = self._weight(channel)
            for rank, (ref_id, raw) in enumerate(ranked, start=1):
                totals[ref_id] = totals.get(ref_id, 0.0) + weight / (k + rank)
                signals.setdefault(ref_id, {})[channel] = round(float(raw), 4)
        fused = [(ref_id, score, signals.get(ref_id, {})) for ref_id, score in totals.items()]
        fused.sort(key=lambda item: -item[1])
        return fused

    def _to_hits(
        self,
        fused: Sequence[tuple[str, float, dict[str, float]]],
        pool: dict[str, Reference],
        channels: dict[str, list[tuple[str, float]]],
        query_vector: np.ndarray | None,
        use_taste: bool,
        limit: int,
    ) -> list[SearchHit]:
        taste_weight = self.config.weight_taste if (use_taste and self.memory) else 0.0
        max_fused = max((score for _, score, _ in fused), default=1.0) or 1.0

        hits: list[SearchHit] = []
        for ref_id, score, signals in fused[: max(limit * 3, RERANK_DEPTH)]:
            ref = pool.get(ref_id) or self.store.get(ref_id)
            if ref is None:
                continue
            normalized = score / max_fused
            if taste_weight and self.memory is not None:
                affinity = self.memory.affinity(self.store.get_vector(ref_id))
                if affinity:
                    signals["taste"] = round(affinity, 4)
                    normalized = (1 - taste_weight) * normalized + taste_weight * affinity
            hits.append(
                SearchHit(
                    reference=ref,
                    score=round(float(normalized), 4),
                    signals=signals,
                    why=_explain(ref, signals, channels),
                )
            )
        hits.sort(key=lambda h: -h.score)
        return hits

    # -- late interaction rerank -----------------------------------------

    def _maxsim_rerank(self, text: str, hits: list[SearchHit]) -> list[SearchHit]:
        if self.cache is None or not hasattr(self.embedder, "embed_text_patches"):
            return hits
        try:
            query_patches = self.embedder.embed_text_patches([text])[0]
        except Exception:
            return hits

        rescored: list[SearchHit] = []
        for hit in hits[:RERANK_DEPTH]:
            key = Cache.key("patches", hit.reference.content_hash, self.embedder.name)
            patches = self.cache.get_vector(key)
            if patches is None or patches.ndim != 2:
                rescored.append(hit)
                continue
            score = maxsim(query_patches, patches)
            hit.signals["maxsim"] = round(float(score), 4)
            hit.score = round(
                0.35 * hit.score + 0.65 * float(np.clip(score, 0.0, 1.0)), 4
            )
            hit.why.append("reranked by late-interaction MaxSim")
            rescored.append(hit)
        rescored.sort(key=lambda h: -h.score)
        return rescored + hits[RERANK_DEPTH:]


def _explain(ref: Reference, signals: dict[str, float], channels: dict[str, Any]) -> list[str]:
    why: list[str] = []
    if "image" in signals:
        why.append(f"visually similar (cos {signals['image']:.2f})")
    if "text" in signals:
        why.append(f"semantic match (cos {signals['text']:.2f})")
    if "lexical" in signals:
        why.append("caption/tag keyword match")
    if "style" in signals:
        why.append(f"style fit {signals['style']:.2f}")
    if "taste" in signals:
        why.append(f"matches your taste memory ({signals['taste']:.2f})")
    style = ref.features.style
    if style.primary != "unclassified":
        why.append(f"classified {style.primary} ({style.confidence:.2f})")
    return why
