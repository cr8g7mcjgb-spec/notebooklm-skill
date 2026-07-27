"""LanceDB store — the recommended local/embedded backend past ~50k references.

LanceDB runs in-process over the Lance columnar format, so there is no server to
operate, the index lives in a directory you can rsync or mount into the Docker
image, and it still gives real ANN + predicate pushdown.

Install with: pip install "design-intel[lancedb]"
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any

import numpy as np

from ..registry import register_store
from ..serde import reference_from_dict
from ..types import Reference
from .base import OVERFETCH, BaseStore, matches

TABLE = "design_references"


@register_store("lancedb")
class LanceDBStore(BaseStore):
    name = "lancedb"

    def __init__(self, path: str = ".design-index/lance", table: str = TABLE, **kwargs: Any) -> None:
        super().__init__(path=path, table=table, **kwargs)
        try:
            import lancedb
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise ImportError("lancedb backend needs: pip install 'design-intel[lancedb]'") from exc
        import pyarrow as pa

        self._pa = pa
        self.db = lancedb.connect(path)
        self.table_name = table
        self._table = self.db.open_table(table) if table in self.db.table_names() else None

    def _schema(self, dim: int) -> Any:
        pa = self._pa
        return pa.schema(
            [
                pa.field("id", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), dim)),
                pa.field("path", pa.string()),
                pa.field("collection", pa.string()),
                pa.field("content_hash", pa.string()),
                pa.field("style", pa.string()),
                pa.field("tags", pa.string()),
                pa.field("record", pa.string()),
            ]
        )

    def _rows(self, references: Sequence[Reference], vectors: np.ndarray) -> list[dict[str, Any]]:
        return [
            {
                "id": ref.id,
                "vector": vec.astype(np.float32).tolist(),
                "path": ref.path,
                "collection": ref.collection,
                "content_hash": ref.content_hash,
                "style": ref.features.style.primary,
                "tags": ",".join(ref.tags),
                "record": json.dumps(ref.to_dict(), ensure_ascii=False),
            }
            for ref, vec in zip(references, vectors, strict=True)
        ]

    def upsert(self, references: Sequence[Reference], vectors: np.ndarray) -> None:
        vectors = np.asarray(vectors, dtype=np.float32)
        if vectors.ndim == 1:
            vectors = vectors[None, :]
        rows = self._rows(references, vectors)
        if not rows:
            return
        if self._table is None:
            self._table = self.db.create_table(
                self.table_name, data=rows, schema=self._schema(vectors.shape[1])
            )
            return
        (
            self._table.merge_insert("id")
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute(rows)
        )

    def search(
        self, vector: np.ndarray, limit: int = 10, filters: dict[str, Any] | None = None
    ) -> list[tuple[Reference, float]]:
        if self._table is None:
            return []
        query = np.asarray(vector, dtype=np.float32).ravel()
        query = query / max(float(np.linalg.norm(query)), 1e-8)

        builder = self._table.search(query).metric("cosine").limit(limit * OVERFETCH)
        collection = (filters or {}).get("collection")
        if isinstance(collection, str):  # the one predicate worth pushing down
            builder = builder.where(f"collection = '{_escape(collection)}'", prefilter=True)

        out: list[tuple[Reference, float]] = []
        for row in builder.to_list():
            ref = reference_from_dict(json.loads(row["record"]))
            if not matches(ref, filters):
                continue
            out.append((ref, 1.0 - float(row.get("_distance", 0.0))))
            if len(out) >= limit:
                break
        return out

    def get(self, ref_id: str) -> Reference | None:
        if self._table is None:
            return None
        rows = self._table.search().where(f"id = '{_escape(ref_id)}'").limit(1).to_list()
        return reference_from_dict(json.loads(rows[0]["record"])) if rows else None

    def get_vector(self, ref_id: str) -> np.ndarray | None:
        if self._table is None:
            return None
        rows = self._table.search().where(f"id = '{_escape(ref_id)}'").limit(1).to_list()
        return np.asarray(rows[0]["vector"], dtype=np.float32) if rows else None

    def delete(self, ref_ids: Sequence[str]) -> int:
        if self._table is None or not ref_ids:
            return 0
        quoted = ", ".join(f"'{_escape(r)}'" for r in ref_ids)
        before = self._table.count_rows()
        self._table.delete(f"id IN ({quoted})")
        return before - self._table.count_rows()

    def iter_references(self, collection: str | None = None) -> Iterable[Reference]:
        if self._table is None:
            return
        table = self._table.to_arrow()
        for record, coll in zip(table["record"].to_pylist(), table["collection"].to_pylist(), strict=False):
            if collection is None or coll == collection:
                yield reference_from_dict(json.loads(record))

    def count(self, collection: str | None = None) -> int:
        if self._table is None:
            return 0
        if collection is None:
            return int(self._table.count_rows())
        return sum(1 for _ in self.iter_references(collection))


def _escape(value: str) -> str:
    return value.replace("'", "''")
