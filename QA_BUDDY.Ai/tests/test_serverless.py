"""The Vercel mode: an in-memory snapshot index and embeddings from an OpenAI-compatible API."""

import dataclasses
import gzip
import json
import math
from array import array

import httpx

from qabuddy.embeddings import REQUEST_TOKEN, Embedder, EmbeddingError
from qabuddy.retrieval import Retriever
from qabuddy.snapshot import SnapshotStore, open_store
from qabuddy.sparse import encode_document, encode_query


def _write_snapshot(folder, docs, seed=None):
    """docs: (id, source, text, dense vector)."""
    folder.mkdir(parents=True, exist_ok=True)
    dense = array("f")
    for _, _, _, vector in docs:
        norm = math.sqrt(sum(x * x for x in vector))
        dense.extend(x / norm for x in vector)
    (folder / "dense.f32").write_bytes(dense.tobytes())
    sparse = [[encode_document(text).indices, encode_document(text).values] for _, _, text, _ in docs]
    payloads = [{"source": source, "text": text, "title": text.split(":")[0], "doc_id": f"{source}/{i}"} for i, source, text, _ in docs]
    with gzip.open(folder / "points.json.gz", "wt", encoding="utf-8") as handle:
        json.dump({"ids": [d[0] for d in docs], "payloads": payloads, "sparse": sparse}, handle)
    if seed:
        with gzip.open(folder / "queries.json.gz", "wt", encoding="utf-8") as handle:
            json.dump(seed, handle)
    (folder / "meta.json").write_text(json.dumps({"collection": "qabuddy", "count": len(docs), "dim": len(docs[0][3]), "exported": "2026-10-09T20:00:00"}))


DOCS = [
    ("a", "jira", "QAB-101: CI login fails after agent migration", [1.0, 0.0, 0.0]),
    ("b", "selenium", "RetryAnalyzer.java: retries a failed test three times", [0.0, 1.0, 0.0]),
    ("c", "test_cases", "INVALID-016: email containing only spaces", [0.0, 0.0, 1.0]),
]


def test_snapshot_store_scores_like_qdrant(tmp_path):
    _write_snapshot(tmp_path, DOCS)
    store = SnapshotStore(tmp_path)
    assert store.count() == 3 and store.counts_by("source") == {"jira": 1, "selenium": 1, "test_cases": 1}
    results = store.search([0.1, 0.9, 0.0], encode_query("INVALID-016"), None, 2)
    assert [h.id for h in results["semantic"]] == ["b", "a"]
    assert [h.id for h in results["keyword"]] == ["c"]  # only chunks sharing a term are returned
    filtered = store.search([0.1, 0.9, 0.0], None, ["jira"], 5)
    assert [h.id for h in filtered["semantic"]] == ["a"]
    assert store.get("c").payload["source"] == "test_cases" and store.get("zzz") is None


def test_open_store_picks_the_snapshot(settings, tmp_path):
    _write_snapshot(tmp_path, DOCS)
    assert isinstance(open_store(dataclasses.replace(settings, snapshot_dir=tmp_path)), SnapshotStore)


def _remote_settings(settings, tmp_path, key=""):
    return dataclasses.replace(
        settings, snapshot_dir=tmp_path, embed_api_url="https://gateway.test/v1", embed_api_key=key, embed_api_model="alibaba/qwen3-embedding-0.6b"
    )


def test_remote_embeddings_use_the_request_token(settings, tmp_path):
    _write_snapshot(tmp_path, DOCS)
    seen = []

    def handler(request):
        seen.append(request.headers["authorization"])
        body = json.loads(request.content)
        return httpx.Response(200, json={"data": [{"index": i, "embedding": [0.5, 0.5, 0.0]} for i, _ in enumerate(body["input"])]})

    embedder = Embedder(_remote_settings(settings, tmp_path))
    embedder._client = httpx.Client(base_url="https://gateway.test/v1", transport=httpx.MockTransport(handler))
    token = REQUEST_TOKEN.set("oidc-token-from-vercel")
    try:
        assert embedder.embed_query("how do retries work?") == [0.5, 0.5, 0.0]
    finally:
        REQUEST_TOKEN.reset(token)
    assert seen == ["Bearer oidc-token-from-vercel"]


def test_seeded_questions_need_no_api_and_others_fall_back_to_keywords(settings, tmp_path):
    _write_snapshot(tmp_path, DOCS, seed={"Why did CI login fail?": [1.0, 0.0, 0.0]})
    s = _remote_settings(settings, tmp_path, key="gateway-key")

    def refuse(request):
        return httpx.Response(403, json={"error": {"message": "AI Gateway requires a valid credit card on file"}})

    embedder = Embedder(s)
    embedder._client = httpx.Client(base_url="https://gateway.test/v1", transport=httpx.MockTransport(refuse))
    retriever = Retriever(s, embedder=embedder)

    seeded = retriever.search("Why did CI login fail?", top_k=3)
    assert "semantic" in seeded[0].ranks and seeded[0].id == "a"

    other = retriever.search("INVALID-016", top_k=3)  # not seeded: the gateway refuses, keywords answer
    assert other[0].id == "c" and set(other[0].ranks) == {"keyword"}

    try:
        embedder.embed_query("anything new")
        raised = False
    except EmbeddingError as error:
        raised = "credit card" in str(error)
    assert raised
    assert "example questions only" in embedder.health()["note"]
