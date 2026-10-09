"""A read-only, in-memory copy of the index, for serverless hosting (Vercel).

Qdrant stays the system of record. `python -m qabuddy export-snapshot` writes
every point (unit-length dense vector, BM25 sparse vector, payload) to a
folder, and SnapshotStore searches that folder in memory with the scoring
Qdrant uses: cosine similarity for the semantic side, and term weight x query
weight x IDF for the keyword side, with IDF computed over the snapshot exactly
like Qdrant's `Modifier.IDF`. A few thousand chunks load in under a second, so
a serverless function needs no database at all.
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import math
import operator
from array import array
from collections import Counter
from pathlib import Path

from .models import Hit
from .settings import Settings
from .sparse import SparseVector

DENSE_FILE = "dense.f32"
POINTS_FILE = "points.json.gz"
META_FILE = "meta.json"
QUERIES_FILE = "queries.json.gz"  # question -> vector, embedded at export time with the index's own model


def load_query_seed(folder: Path) -> dict[str, list[float]]:
    path = Path(folder) / QUERIES_FILE
    if not path.exists():
        return {}
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def open_store(settings: Settings):
    """The index to search: a snapshot folder when one is configured, otherwise Qdrant."""
    if settings.snapshot_dir:
        return SnapshotStore(settings.snapshot_dir)
    from .store import Store  # imported lazily: serverless builds don't ship the Qdrant client

    return Store(settings)


class SnapshotStore:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.meta = json.loads((self.folder / META_FILE).read_text(encoding="utf-8"))
        self.collection = self.meta.get("collection", "snapshot")
        dim = int(self.meta["dim"])
        raw = array("f")
        raw.frombytes((self.folder / DENSE_FILE).read_bytes())
        with gzip.open(self.folder / POINTS_FILE, "rt", encoding="utf-8") as handle:
            data = json.load(handle)
        self.ids: list[str] = data["ids"]
        self.payloads: list[dict] = data["payloads"]
        self.dense = [raw[k * dim : (k + 1) * dim] for k in range(len(self.ids))]
        self.sparse = [dict(zip(indices, values)) for indices, values in data["sparse"]]
        self.position = {pid: k for k, pid in enumerate(self.ids)}
        n = len(self.ids)
        frequency = Counter(index for vector in self.sparse for index in vector)
        self.idf = {index: math.log((n - df + 0.5) / (df + 0.5) + 1.0) for index, df in frequency.items()}

    def search(
        self,
        dense: list[float] | None,
        sparse: SparseVector | None,
        sources: list[str] | None,
        limit: int,
    ) -> dict[str, list[Hit]]:
        allowed = [k for k, p in enumerate(self.payloads) if not sources or p.get("source") in sources]
        results: dict[str, list[Hit]] = {}
        if dense is not None:
            norm = math.sqrt(sum(x * x for x in dense)) or 1.0
            query = [x / norm for x in dense]
            scored = sorted(((sum(map(operator.mul, query, self.dense[k])), k) for k in allowed), reverse=True)
            results["semantic"] = [Hit(self.ids[k], score, self.payloads[k]) for score, k in scored[:limit]]
        if sparse is not None and sparse.indices:
            terms = list(zip(sparse.indices, sparse.values))
            scored = []
            for k in allowed:
                document = self.sparse[k]
                score = sum(weight * document[i] * self.idf[i] for i, weight in terms if i in document)
                if score > 0:
                    scored.append((score, k))
            scored.sort(reverse=True)
            results["keyword"] = [Hit(self.ids[k], score, self.payloads[k]) for score, k in scored[:limit]]
        return results

    def get(self, point_id: str) -> Hit | None:
        k = self.position.get(point_id)
        return Hit(point_id, 1.0, self.payloads[k]) if k is not None else None

    def count(self) -> int:
        return len(self.ids)

    def counts_by(self, field: str) -> dict[str, int]:
        return dict(Counter(str(p.get(field)) for p in self.payloads if p.get(field) is not None))

    def exists(self) -> bool:
        return True

    def health(self) -> dict:
        return {"ok": True, "engine": "in-memory snapshot", "collection": self.collection, "exists": True}


def export_snapshot(settings: Settings, folder: Path, seed_questions: list[str] | None = None) -> dict:
    """Write the whole Qdrant collection to `folder` for SnapshotStore.

    `seed_questions` (for example the UI's example questions) are embedded now, with
    the same local model as the index, so they get full hybrid search even where the
    deployment has no embedding model of its own.
    """
    from .embeddings import Embedder
    from .store import DENSE, SPARSE, Store

    store = Store(settings)
    if not store.exists():
        raise RuntimeError(f"Collection '{settings.collection}' does not exist. Run: python -m qabuddy ingest")
    points, offset = [], None
    while True:
        batch, offset = store.client.scroll(
            store.collection, limit=256, offset=offset, with_payload=True, with_vectors=True
        )
        points.extend(batch)
        if offset is None:
            break
    points.sort(key=lambda p: (str(p.payload.get("doc_id", "")), int(p.payload.get("chunk", 0))))

    dense = array("f")
    ids, payloads, sparse = [], [], []
    for point in points:
        vector = point.vector[DENSE]
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        dense.extend(x / norm for x in vector)
        keyword = point.vector[SPARSE]
        sparse.append([list(keyword.indices), [round(v, 5) for v in keyword.values]])
        ids.append(str(point.id))
        payloads.append(point.payload)

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / DENSE_FILE).write_bytes(dense.tobytes())
    with gzip.open(folder / POINTS_FILE, "wt", encoding="utf-8") as handle:
        json.dump({"ids": ids, "payloads": payloads, "sparse": sparse}, handle, ensure_ascii=False, separators=(",", ":"))
    seed = {}
    if seed_questions:
        embedder = Embedder(settings)
        seed = {q: [round(x, 6) for x in embedder.embed_query(q)] for q in dict.fromkeys(seed_questions)}
        with gzip.open(folder / QUERIES_FILE, "wt", encoding="utf-8") as handle:
            json.dump(seed, handle, separators=(",", ":"))
    last_ingest = settings.storage_dir / "last_ingest.json"
    meta = {
        "collection": settings.collection,
        "count": len(points),
        "dim": len(points[0].vector[DENSE]) if points else 0,
        "embed_model": settings.embed_model,
        "seeded_questions": len(seed),
        "exported": dt.datetime.now().isoformat(timespec="seconds"),
        "last_ingest": json.loads(last_ingest.read_text(encoding="utf-8")).get("finished") if last_ingest.exists() else None,
    }
    (folder / META_FILE).write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta
