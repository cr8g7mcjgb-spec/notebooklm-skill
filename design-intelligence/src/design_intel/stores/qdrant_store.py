"""Qdrant store — the cloud / shared-team backend.

Same code path for a local `docker compose up qdrant` and for Qdrant Cloud;
only the URL and api key change. Collection, style and tag filters are pushed
into Qdrant's payload filter so they run server-side; the richer numeric feature
filters are applied after retrieval.

Install with: pip install "design-intel[qdrant]"
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from typing import Any

import numpy as np

from ..registry import register_store
from ..serde import reference_from_dict
from ..types import Reference
from .base import OVERFETCH, BaseStore, matches

COLLECTION = "design_references"


@register_store("qdrant")
class QdrantStore(BaseStore):
    name = "qdrant"

    def __init__(
        self,
        url: str = "http://localhost:6333",
        collection: str = COLLECTION,
        api_key: str | None = None,
        dim: int = 0,
        **kwargs: Any,
    ) -> None:
        super().__init__(url=url, collection=collection, **kwargs)
        try:
            from qdrant_client import QdrantClient, models
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise ImportError("qdrant backend needs: pip install 'design-intel[qdrant]'") from exc

        self._models = models
        self.client = QdrantClient(url=url, api_key=api_key, timeout=30)
        self.collection = collection
        self._dim = dim

    def _ensure(self, dim: int) -> None:
        models = self._models
        if self.client.collection_exists(self.collection):
            return
        self.client.create_collection(
            collection_name=self.collection,
            vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
        )
        for field in ("collection", "style", "content_hash", "path"):
            try:
                self.client.create_payload_index(self.collection, field, field_schema="keyword")
            except Exception:
                pass  # index already present, or an older server — search still works

    @staticmethod
    def _point_id(ref_id: str) -> str:
        """Qdrant wants a UUID or int; derive one deterministically from our id."""
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"design-intel:{ref_id}"))

    def upsert(self, references: Sequence[Reference], vectors: np.ndarray) -> None:
        vectors = np.asarray(vectors, dtype=np.float32)
        if vectors.ndim == 1:
            vectors = vectors[None, :]
        if not len(references):
            return
        self._ensure(vectors.shape[1])
        models = self._models
        points = [
            models.PointStruct(
                id=self._point_id(ref.id),
                vector=vec.astype(np.float32).tolist(),
                payload={
                    "collection": ref.collection,
                    "style": ref.features.style.primary,
                    "content_hash": ref.content_hash,
                    "path": ref.path,
                    "tags": ref.tags,
                    "record": ref.to_dict(),
                },
            )
            for ref, vec in zip(references, vectors, strict=True)
        ]
        self.client.upsert(self.collection, points=points, wait=True)

    def _server_filter(self, filters: dict[str, Any] | None) -> Any:
        if not filters:
            return None
        models = self._models
        must = []
        collection = filters.get("collection")
        if isinstance(collection, str):
            must.append(models.FieldCondition(key="collection", match=models.MatchValue(value=collection)))
        styles = filters.get("styles")
        if styles:
            values = [styles] if isinstance(styles, str) else list(styles)
            must.append(models.FieldCondition(key="style", match=models.MatchAny(any=values)))
        tags = filters.get("tags")
        if tags:
            must.append(models.FieldCondition(key="tags", match=models.MatchAny(any=list(tags))))
        return models.Filter(must=must) if must else None

    def search(
        self, vector: np.ndarray, limit: int = 10, filters: dict[str, Any] | None = None
    ) -> list[tuple[Reference, float]]:
        if not self.client.collection_exists(self.collection):
            return []
        query = np.asarray(vector, dtype=np.float32).ravel()
        query = query / max(float(np.linalg.norm(query)), 1e-8)
        response = self.client.query_points(
            self.collection,
            query=query.tolist(),
            limit=limit * OVERFETCH,
            query_filter=self._server_filter(filters),
            with_payload=True,
        )
        out: list[tuple[Reference, float]] = []
        for point in response.points:
            ref = reference_from_dict((point.payload or {}).get("record", {}))
            if not matches(ref, filters):
                continue
            out.append((ref, float(point.score)))
            if len(out) >= limit:
                break
        return out

    def get(self, ref_id: str) -> Reference | None:
        if not self.client.collection_exists(self.collection):
            return None
        points = self.client.retrieve(self.collection, ids=[self._point_id(ref_id)], with_payload=True)
        if not points:
            return None
        return reference_from_dict((points[0].payload or {}).get("record", {}))

    def get_vector(self, ref_id: str) -> np.ndarray | None:
        if not self.client.collection_exists(self.collection):
            return None
        points = self.client.retrieve(
            self.collection, ids=[self._point_id(ref_id)], with_vectors=True
        )
        if not points or points[0].vector is None:
            return None
        return np.asarray(points[0].vector, dtype=np.float32)

    def delete(self, ref_ids: Sequence[str]) -> int:
        if not ref_ids or not self.client.collection_exists(self.collection):
            return 0
        self.client.delete(
            self.collection,
            points_selector=self._models.PointIdsList(points=[self._point_id(r) for r in ref_ids]),
            wait=True,
        )
        return len(ref_ids)

    def iter_references(self, collection: str | None = None) -> Iterable[Reference]:
        if not self.client.collection_exists(self.collection):
            return
        offset = None
        while True:
            points, offset = self.client.scroll(
                self.collection,
                limit=256,
                offset=offset,
                with_payload=True,
                scroll_filter=self._server_filter({"collection": collection} if collection else None),
            )
            for point in points:
                yield reference_from_dict((point.payload or {}).get("record", {}))
            if offset is None:
                break

    def count(self, collection: str | None = None) -> int:
        if not self.client.collection_exists(self.collection):
            return 0
        if collection is None:
            return int(self.client.count(self.collection, exact=True).count)
        return int(
            self.client.count(
                self.collection, count_filter=self._server_filter({"collection": collection}), exact=True
            ).count
        )
