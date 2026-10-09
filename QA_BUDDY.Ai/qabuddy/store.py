"""Qdrant: one collection, a dense and a BM25 sparse vector per chunk."""

from __future__ import annotations

from qdrant_client import QdrantClient, models

from .models import Hit
from .settings import Settings
from .sparse import SparseVector

DENSE = "dense"
SPARSE = "bm25"
INDEXED_FIELDS = ("source", "source_type", "doc_id")

__all__ = ["DENSE", "SPARSE", "Hit", "Store"]


class Store:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.collection = settings.collection
        self.client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None, timeout=60)

    # --- collection -------------------------------------------------------------------

    def exists(self) -> bool:
        return self.client.collection_exists(self.collection)

    def ensure(self, dimension: int) -> bool:
        """Create the collection if needed. Returns True when it was created."""
        if self.exists():
            info = self.client.get_collection(self.collection)
            vectors = info.config.params.vectors
            current = vectors[DENSE].size if isinstance(vectors, dict) else None
            if current != dimension:
                raise RuntimeError(
                    f"Collection '{self.collection}' holds {current}-dim vectors but the embedding model "
                    f"gives {dimension}. Re-index with: python -m qabuddy ingest --rebuild"
                )
            return False
        self.client.create_collection(
            self.collection,
            vectors_config={DENSE: models.VectorParams(size=dimension, distance=models.Distance.COSINE)},
            sparse_vectors_config={SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)},
        )
        for field in INDEXED_FIELDS:
            self.client.create_payload_index(self.collection, field, models.PayloadSchemaType.KEYWORD)
        return True

    def drop(self) -> None:
        if self.exists():
            self.client.delete_collection(self.collection)

    # --- writes -------------------------------------------------------------------------

    def upsert(self, ids: list[str], dense: list[list[float]], sparse: list[SparseVector], payloads: list[dict]) -> None:
        points = [
            models.PointStruct(
                id=pid,
                vector={DENSE: d, SPARSE: models.SparseVector(indices=s.indices, values=s.values)},
                payload=p,
            )
            for pid, d, s, p in zip(ids, dense, sparse, payloads)
        ]
        for start in range(0, len(points), 128):
            self.client.upsert(self.collection, points=points[start : start + 128], wait=True)

    def delete_docs(self, doc_ids: list[str]) -> None:
        if not doc_ids or not self.exists():
            return
        for start in range(0, len(doc_ids), 200):
            self.client.delete(
                self.collection,
                points_selector=models.FilterSelector(
                    filter=models.Filter(must=[models.FieldCondition(key="doc_id", match=models.MatchAny(any=doc_ids[start : start + 200]))])
                ),
                wait=True,
            )

    # --- reads --------------------------------------------------------------------------

    def search(
        self,
        dense: list[float] | None,
        sparse: SparseVector | None,
        sources: list[str] | None,
        limit: int,
    ) -> dict[str, list[Hit]]:
        """Run the semantic and the keyword query in one round trip; fusion happens in retrieval."""
        flt = None
        if sources:
            flt = models.Filter(must=[models.FieldCondition(key="source", match=models.MatchAny(any=sources))])
        requests, names = [], []
        if dense is not None:
            requests.append(models.QueryRequest(query=dense, using=DENSE, filter=flt, limit=limit, with_payload=True))
            names.append("semantic")
        if sparse is not None and sparse.indices:
            query = models.SparseVector(indices=sparse.indices, values=sparse.values)
            requests.append(models.QueryRequest(query=query, using=SPARSE, filter=flt, limit=limit, with_payload=True))
            names.append("keyword")
        if not requests:
            return {}
        responses = self.client.query_batch_points(self.collection, requests=requests)
        return {
            name: [Hit(str(p.id), p.score, p.payload or {}) for p in response.points]
            for name, response in zip(names, responses)
        }

    def get(self, point_id: str) -> Hit | None:
        points = self.client.retrieve(self.collection, ids=[point_id], with_payload=True)
        return Hit(str(points[0].id), 1.0, points[0].payload or {}) if points else None

    def count(self) -> int:
        return self.client.count(self.collection, exact=True).count if self.exists() else 0

    def counts_by(self, field: str) -> dict[str, int]:
        if not self.exists():
            return {}
        result = self.client.facet(self.collection, key=field, limit=1000, exact=True)
        return {str(h.value): h.count for h in result.hits}

    def health(self) -> dict:
        try:
            self.client.get_collections()
        except Exception as error:  # connection refused, timeouts, auth
            return {"ok": False, "error": f"Qdrant not reachable at {self.settings.qdrant_url}: {error}"}
        return {"ok": True, "engine": "Qdrant", "collection": self.collection, "exists": self.exists()}
