"""Hybrid retrieval: semantic (Qwen3 dense) + keyword (BM25 sparse), fused with RRF.

Both searches run in one Qdrant round trip. Reciprocal Rank Fusion
(score = sum of 1 / (60 + rank)) needs no score calibration between the two,
and the ranks are kept per hit so the UI can show *why* a chunk was found.

Plain RRF favours chunks that both searches rank in the middle over a chunk
that one search ranks first. With hundreds of look-alike test cases that buries
exact lookups, so two rules come first:

1. a test case ID, ticket key or file name named in the question is pinned to the top;
2. each search's best hit is always kept, right after the pinned ones.

Near-duplicates (the same test case in two files, overlapping log windows)
are dropped before the final top-k.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field

from .embeddings import Embedder, EmbeddingError
from .rerank import Reranker
from .settings import Settings
from .snapshot import open_store
from .sparse import encode_query
from .text import Glossary

log = logging.getLogger(__name__)

RRF_K = 60
_LINES = re.compile(r"L(\d+)-L(\d+)")
_ID = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*-\d+\b")  # WING-LOGIN-TC-042, QAB-102
_FILE = re.compile(r"\b[\w.\-]+\.(?:java|tsx?|jsx?|mjs|py|xml|json|md|properties|ya?ml|csv|xlsx|pdf|feature|log)\b", re.I)


@dataclass
class Retrieved:
    id: str
    score: float
    payload: dict
    ranks: dict[str, int] = field(default_factory=dict)  # retriever -> 1-based rank
    rerank_score: float | None = None


class Retriever:
    def __init__(self, settings: Settings, store=None, embedder: Embedder | None = None):
        self.settings = settings
        self.store = store or open_store(settings)
        self.embedder = embedder or Embedder(settings)
        self.glossary = Glossary.load(settings.glossary_file)
        self.reranker = Reranker.from_settings(settings)

    def search(
        self,
        query: str,
        sources: list[str] | None = None,
        top_k: int | None = None,
        mode: str = "hybrid",
    ) -> list[Retrieved]:
        top_k = top_k or self.settings.top_k
        dense = None
        if mode in ("hybrid", "semantic"):
            try:
                dense = self.embedder.embed_query(query)
            except EmbeddingError as error:
                if mode == "semantic":
                    raise
                # Keyword search alone still answers most questions; better than failing.
                log.warning("Semantic search unavailable, using keyword search only: %s", error)
        sparse = encode_query(query, self.glossary.expansions(query)) if mode in ("hybrid", "keyword") else None
        results = self.store.search(dense, sparse, sources, self.settings.candidates)
        fused = fuse(results, query)
        if self.reranker:
            fused = self.reranker.rerank(query, fused[: max(top_k * 3, 20)])
        return deduplicate(fused)[:top_k]


def fuse(results: dict, query: str = "") -> list[Retrieved]:
    merged: dict[str, Retrieved] = {}
    for name, hits in results.items():
        for rank, hit in enumerate(hits, 1):
            item = merged.setdefault(hit.id, Retrieved(hit.id, 0.0, hit.payload))
            item.score += 1.0 / (RRF_K + rank)
            item.ranks[name] = rank
    ordered = sorted(merged.values(), key=lambda r: r.score, reverse=True)

    named = exact_names(query)
    pinned = [h for h in ordered if named and _names(h.payload) & named]
    best = [merged[hits[0].id] for hits in results.values() if hits]
    best = sorted({h.id: h for h in best if h not in pinned}.values(), key=lambda r: r.score, reverse=True)
    lead = pinned + best
    return lead + [h for h in ordered if h not in lead]


def exact_names(query: str) -> set[str]:
    """Identifiers and file names the question asks about by name."""
    return {m.lower() for m in _ID.findall(query)} | {m.lower() for m in _FILE.findall(query)}


def _names(payload: dict) -> set[str]:
    meta = payload.get("meta") or {}
    title = str(payload.get("title") or "").lower()
    names = {title, title.split(":", 1)[0].strip(), str(payload.get("doc_id") or "").rsplit("/", 1)[-1].lower()}
    names.update(str(meta[k]).lower() for k in ("test_id", "key") if meta.get(k))
    return names


def deduplicate(hits: list[Retrieved]) -> list[Retrieved]:
    kept: list[Retrieved] = []
    seen_text: set[str] = set()
    for hit in hits:
        body = hit.payload.get("text", "")
        digest = hashlib.sha1(body.split("\n", 2)[-1].encode("utf-8")).hexdigest()
        if digest in seen_text:
            continue
        if any(_overlaps(hit.payload, k.payload) for k in kept):
            continue
        seen_text.add(digest)
        kept.append(hit)
    return kept


def _overlaps(a: dict, b: dict) -> bool:
    """Two windows of the same file that share more than half their lines."""
    if a.get("doc_id") != b.get("doc_id"):
        return False
    ma, mb = _LINES.search(a.get("location", "")), _LINES.search(b.get("location", ""))
    if not ma or not mb:
        return False
    a1, a2, b1, b2 = int(ma[1]), int(ma[2]), int(mb[1]), int(mb[2])
    shared = min(a2, b2) - max(a1, b1) + 1
    return shared > 0 and shared > 0.5 * min(a2 - a1 + 1, b2 - b1 + 1)
