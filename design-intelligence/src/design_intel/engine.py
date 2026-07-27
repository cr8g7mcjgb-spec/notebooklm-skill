"""The façade. One object that the CLI, the MCP server and library users share.

Components are built lazily: importing `DesignIntelligence` never loads torch,
never opens a vector store, and never touches the network. The first call that
needs an embedder builds one.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from .analysis.pipeline import analyze_image
from .cache import Cache
from .config import Config
from .index.indexer import Indexer, IndexReport
from .memory.taste import TasteMemory
from .patterns.brief import build_brief
from .patterns.compare import compare_features, critique
from .patterns.consensus import find_common_patterns
from .registry import get_embedder_class, get_store_class
from .search.hybrid import HybridSearcher
from .stores.base import BaseStore
from .style.taxonomy import STYLES, get_style
from .types import ComparisonResult, DesignFeatures, PatternReport, Reference, SearchHit


class DesignIntelligence:
    def __init__(self, config: Config | None = None, **overrides: Any) -> None:
        self.config = config or Config.load(**overrides)
        self._store: BaseStore | None = None
        self._embedder: Any | None = None
        self._memory: TasteMemory | None = None
        self._cache: Cache | None = None
        self._searcher: HybridSearcher | None = None

    # -- lazily constructed components ------------------------------------

    @property
    def cache(self) -> Cache:
        if self._cache is None:
            self._cache = Cache(self.config.cache_dir, enabled=self.config.cache_enabled)
        return self._cache

    @property
    def embedder(self) -> Any:
        if self._embedder is None:
            cls = get_embedder_class(self.config.embedder)
            self._embedder = cls(**self.config.embedder_options)
        return self._embedder

    @property
    def store(self) -> BaseStore:
        if self._store is None:
            cls = get_store_class(self.config.store)
            self._store = cls(**self.config.resolved_store_options())
        return self._store

    @property
    def memory(self) -> TasteMemory:
        if self._memory is None:
            self._memory = TasteMemory(self.config.memory_path, enabled=self.config.memory_enabled)
        return self._memory

    @property
    def searcher(self) -> HybridSearcher:
        if self._searcher is None:
            self._searcher = HybridSearcher(
                store=self.store,
                embedder=self.embedder,
                config=self.config,
                memory=self.memory,
                cache=self.cache,
            )
        return self._searcher

    # -- indexing ---------------------------------------------------------

    def index(self, paths: Sequence[str | Path], **kwargs: Any) -> IndexReport:
        indexer = Indexer(
            store=self.store,
            embedder=self.embedder,
            cache=self.cache,
            max_workers=self.config.max_workers,
            collection=self.config.collection,
        )
        return indexer.index(paths, **kwargs)

    def annotate(
        self,
        ref_id: str,
        caption: str | None = None,
        tags: Sequence[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Reference:
        """Attach human context to an indexed reference (drives lexical search)."""
        ref = self.store.get(ref_id)
        if ref is None:
            raise KeyError(f"unknown reference id: {ref_id}")
        vector = self.store.get_vector(ref_id)
        if vector is None:
            raise KeyError(f"reference {ref_id} has no stored vector")
        if caption is not None:
            ref.caption = caption
        if tags:
            ref.tags = sorted(set(ref.tags) | set(tags))
        if metadata:
            ref.metadata.update(metadata)
        self.store.upsert([ref], vector[None, :])
        return ref

    # -- retrieval ---------------------------------------------------------

    def search(self, **kwargs: Any) -> list[SearchHit]:
        return self.searcher.search(**kwargs)

    # -- analysis ----------------------------------------------------------

    def features_of(self, target: str | Path) -> tuple[DesignFeatures, Reference | None]:
        """Accept an indexed reference id or a path to an image on disk."""
        ref = self.store.get(str(target))
        if ref is not None:
            return ref.features, ref

        path = Path(str(target)).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"not an indexed id and not a file on disk: {target}")

        from .cache import file_hash

        digest = file_hash(path)
        key = Cache.key("analysis", digest, "adhoc", self.embedder.name)
        cached = self.cache.get_json(key)
        if cached is not None:
            from .serde import features_from_dict

            return features_from_dict(cached), None

        vector = self.embedder.embed_images([path])[0]
        features = analyze_image(path, embedder=self.embedder, image_vector=vector)
        self.cache.set_json(key, features.to_dict())
        self.cache.set_vector(Cache.key("vec", digest, self.embedder.name, self.embedder.dim), vector)
        return features, None

    def extract_style(self, target: str | Path) -> dict[str, Any]:
        features, ref = self.features_of(target)
        style = get_style(features.style.primary)
        return {
            "target": str(target),
            "indexed": ref is not None,
            "style": features.style.to_dict(),
            "definition": style.to_dict() if style else None,
            "features": features.to_dict(),
            "summary": summarize(features),
        }

    def compare(self, a: str | Path, b: str | Path) -> ComparisonResult:
        features_a, ref_a = self.features_of(a)
        features_b, ref_b = self.features_of(b)

        similarity = None
        vec_a = self._vector_for(a, ref_a)
        vec_b = self._vector_for(b, ref_b)
        if vec_a is not None and vec_b is not None and vec_a.shape == vec_b.shape:
            similarity = float(vec_a @ vec_b)

        return compare_features(features_a, features_b, embedding_similarity=similarity)

    def _vector_for(self, target: str | Path, ref: Reference | None) -> np.ndarray | None:
        if ref is not None:
            vector = self.store.get_vector(ref.id)
            if vector is not None:
                return vector
        path = Path(str(target)).expanduser()
        if not path.exists():
            return None
        from .cache import file_hash

        key = Cache.key("vec", file_hash(path), self.embedder.name, self.embedder.dim)
        cached = self.cache.get_vector(key)
        if cached is not None:
            return cached
        vector = self.embedder.embed_images([path])[0]
        self.cache.set_vector(key, vector)
        return vector

    # -- pattern reasoning --------------------------------------------------

    def references_for(
        self,
        ids: Sequence[str] | None = None,
        limit: int = 8,
        **search_kwargs: Any,
    ) -> list[Reference]:
        if ids:
            refs = [self.store.get(i) for i in ids]
            missing = [i for i, r in zip(ids, refs, strict=False) if r is None]
            if missing:
                raise KeyError(f"unknown reference ids: {', '.join(missing)}")
            return [r for r in refs if r is not None]
        hits = self.search(limit=limit, **search_kwargs)
        return [hit.reference for hit in hits]

    def find_common_patterns(
        self,
        ids: Sequence[str] | None = None,
        limit: int = 8,
        min_agreement: float = 0.62,
        **search_kwargs: Any,
    ) -> PatternReport:
        refs = self.references_for(ids=ids, limit=limit, **search_kwargs)
        return find_common_patterns(refs, min_agreement=min_agreement)

    def design_brief(
        self,
        intent: str = "",
        ids: Sequence[str] | None = None,
        limit: int = 8,
        apply_taste: bool = True,
        **search_kwargs: Any,
    ) -> dict[str, Any]:
        if not ids and not search_kwargs:
            search_kwargs = {"text": intent} if intent else {}
        report = self.find_common_patterns(ids=ids, limit=limit, **search_kwargs)
        brief = build_brief(report, intent=intent)

        if apply_taste and self.config.memory_enabled:
            profile = self.memory.profile()
            if profile.get("ready"):
                brief["taste"] = profile
                brief["markdown"] += "\n\n" + _taste_section(profile)
        return brief

    def critique(
        self,
        target: str | Path,
        ids: Sequence[str] | None = None,
        limit: int = 8,
        **search_kwargs: Any,
    ) -> dict[str, Any]:
        features, _ = self.features_of(target)
        if not ids and not search_kwargs:
            search_kwargs = {"style": features.style.primary}
        report = self.find_common_patterns(ids=ids, limit=limit, **search_kwargs)
        result = critique(features, report)
        result["target"] = str(target)
        result["compared_against"] = report.sources
        return result

    # -- taste memory --------------------------------------------------------

    def feedback(
        self, ref_id: str, verdict: str, note: str = "", context: str = ""
    ) -> dict[str, Any]:
        ref = self.store.get(ref_id)
        vector = self.store.get_vector(ref_id) if ref else None
        return self.memory.record(ref, verdict, note=note, context=context, vector=vector)

    def taste_profile(self) -> dict[str, Any]:
        return self.memory.profile()

    # -- introspection -------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        by_collection: dict[str, int] = {}
        by_style: dict[str, int] = {}
        for ref in self.store.iter_references():
            by_collection[ref.collection] = by_collection.get(ref.collection, 0) + 1
            key = ref.features.style.primary
            by_style[key] = by_style.get(key, 0) + 1
        return {
            "references": sum(by_collection.values()),
            "collections": by_collection,
            "styles": dict(sorted(by_style.items(), key=lambda kv: -kv[1])),
            "embedder": self.embedder.describe(),
            "store": self.config.store,
            "cache": self.cache.stats(),
            "taste_events": self.memory.count() if self.config.memory_enabled else 0,
        }

    @staticmethod
    def styles() -> list[dict[str, Any]]:
        return [style.to_dict() for style in STYLES]

    def close(self) -> None:
        if self._store is not None:
            self._store.close()


def summarize(features: DesignFeatures) -> str:
    """One paragraph a human (or Claude) can read without parsing JSON."""
    color, layout, grid, typo, comp = (
        features.color,
        features.layout,
        features.grid,
        features.typography,
        features.composition,
    )
    parts = [
        f"{features.style.primary.replace('_', ' ')} "
        f"({features.style.confidence:.2f} confidence)" if features.style.primary != "unclassified"
        else "no dominant style",
        f"{layout.whitespace_ratio * 100:.0f}% whitespace",
        f"{grid.columns}-column grid" if grid.columns > 1 else "no clear column grid",
        f"{typo.hierarchy_depth} type levels at ×{typo.scale_ratio:.2f}"
        if typo.scale_ratio
        else f"{typo.hierarchy_depth} type levels",
        f"{color.hue_count} hue famil{'y' if color.hue_count == 1 else 'ies'} "
        f"({color.harmony}, {color.temperature})",
        f"{color.contrast_ratio:.1f}:1 contrast",
        f"{comp.geometry} geometry",
        f"{typo.dominant_alignment}-aligned text",
    ]
    return "; ".join(parts) + "."


def _taste_section(profile: dict[str, Any]) -> str:
    lines = ["## Your taste memory", f"Learned from {profile['liked']} designs you kept."]
    for style, count in profile.get("preferred_styles", []):
        lines.append(f"- leans **{style.replace('_', ' ')}** ({count} picks)")
    for _, pref in list(profile.get("preferences", {}).items())[:6]:
        lines.append(
            f"- {pref['label']}: you consistently choose ≈{pref['preferred']} "
            f"(consistency {pref['consistency']:.2f})"
        )
    return "\n".join(lines)
