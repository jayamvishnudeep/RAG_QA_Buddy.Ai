"""Dense embeddings from Qwen3-Embedding, served by Ollama, with a local cache.

Documents are embedded as-is; queries get Qwen3's instruction prefix, which
the model was trained with and which improves retrieval. Every vector is
cached in SQLite by (model, text), so re-running ingestion only embeds chunks
that actually changed. That matters on a CPU-only server.

Serverless hosts can't run Ollama. With EMBED_API_URL set, the same model is
called through an OpenAI-compatible embeddings API instead (Vercel's AI
Gateway serves `alibaba/qwen3-embedding-0.6b`), so questions are embedded
exactly like the indexed chunks were.
"""

from __future__ import annotations

import contextvars
import hashlib
import os
import sqlite3
import threading
import time
from array import array
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import httpx

from .settings import Settings

# On Vercel every request carries a short-lived OIDC token that the AI Gateway accepts.
REQUEST_TOKEN: contextvars.ContextVar[str] = contextvars.ContextVar("qabuddy_request_token", default="")


class EmbeddingError(RuntimeError):
    pass


class Embedder:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.remote = bool(settings.embed_api_url)
        self.model = settings.embed_api_model if self.remote else settings.embed_model
        base_url = settings.embed_api_url if self.remote else settings.ollama_url
        self._client = httpx.Client(base_url=base_url, timeout=httpx.Timeout(600, connect=10))
        cache_path = settings.storage_dir / "embedding_cache.sqlite"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(cache_path), check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, vector BLOB)")
        self._lock = threading.Lock()
        self._seed: dict[str, list[float]] = {}
        if settings.snapshot_dir:  # questions embedded at export time, with the index's own model
            from .snapshot import load_query_seed

            self._seed = load_query_seed(settings.snapshot_dir)

    # --- public -----------------------------------------------------------------

    def embed_documents(self, texts: list[str], progress: Callable[[int, int], None] | None = None) -> list[list[float]]:
        keys = [self._key(t) for t in texts]
        cached = self._load(keys)
        missing = [i for i, k in enumerate(keys) if k not in cached]
        done = len(texts) - len(missing)
        if progress:
            progress(done, len(texts))
        batch = max(1, self.settings.embed_batch)
        for start in range(0, len(missing), batch):
            indices = missing[start : start + batch]
            vectors = self._embed([texts[i] for i in indices])
            self._store({keys[i]: v for i, v in zip(indices, vectors)})
            cached.update({keys[i]: v for i, v in zip(indices, vectors)})
            done += len(indices)
            if progress:
                progress(done, len(texts))
        return [cached[k] for k in keys]

    def embed_query(self, question: str) -> list[float]:
        if question in self._seed:
            return self._seed[question]
        text = f"Instruct: {self.settings.query_instruction}\nQuery: {question}"
        key = self._key(text)
        cached = self._load([key])
        if key in cached:
            return cached[key]
        vector = self._embed([text])[0]
        self._store({key: vector})
        return vector

    def dimension(self) -> int:
        return len(self.embed_query("dimension probe"))

    def health(self) -> dict:
        if self.remote:
            return self._remote_health()
        try:
            tags = self._client.get("/api/tags", timeout=5).json()
        except httpx.HTTPError as error:
            return {"ok": False, "error": f"Ollama not reachable at {self.settings.ollama_url}: {error}"}
        names = {m.get("name") for m in tags.get("models", [])} | {m.get("model") for m in tags.get("models", [])}
        wanted = self.model if ":" in self.model else f"{self.model}:latest"
        if wanted not in names:
            return {"ok": False, "error": f"Model {self.model} is not pulled. Run: ollama pull {self.model}"}
        return {"ok": True, "model": self.model}

    def _remote_health(self) -> dict:
        """Probe the embeddings API at most every 10 minutes (a one-word request costs ~nothing)."""
        now = time.time()
        if not getattr(self, "_probe", None) or now - self._probe[0] > 600:
            try:
                self._embed_remote(["ping"])
                self._probe = (now, "")
            except EmbeddingError as error:
                self._probe = (now, str(error))
        health = {"ok": True, "model": f"{self.model} via {urlparse(self.settings.embed_api_url).netloc}"}
        if self._probe[1]:
            scope = "example questions only" if self._seed else "off"
            health["note"] = f"semantic search: {scope}; keyword search answers the rest. {self._probe[1]}"
        return health

    # --- internals ------------------------------------------------------------------

    def _key(self, text: str) -> str:
        return hashlib.sha256(f"{self.model}\x00{text}".encode("utf-8")).hexdigest()

    def _embed(self, texts: list[str]) -> list[list[float]]:
        return self._embed_remote(texts) if self.remote else self._embed_ollama(texts)

    def _embed_remote(self, texts: list[str]) -> list[list[float]]:
        token = self.settings.embed_api_key or REQUEST_TOKEN.get() or os.environ.get("VERCEL_OIDC_TOKEN", "")
        if not token:
            raise EmbeddingError("No credentials for the embeddings API: set EMBED_API_KEY, or run on Vercel (OIDC)")
        for attempt in range(3):
            try:
                response = self._client.post(
                    "/embeddings",
                    json={"model": self.model, "input": texts},
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=60,
                )
                if response.status_code in (401, 403):
                    try:
                        reason = response.json().get("error", {}).get("message", "")
                    except ValueError:
                        reason = ""
                    raise EmbeddingError(f"The embeddings API refused the request ({response.status_code}). {reason[:200]}".strip())
                response.raise_for_status()
                rows = sorted(response.json().get("data", []), key=lambda row: row.get("index", 0))
                vectors = [row["embedding"] for row in rows]
                if len(vectors) != len(texts):
                    raise EmbeddingError(f"The embeddings API returned {len(vectors)} vectors for {len(texts)} texts")
                return vectors
            except (httpx.TransportError, httpx.HTTPStatusError) as error:
                if attempt == 2:
                    raise EmbeddingError(f"Embeddings API request failed: {error}") from error
                time.sleep(1 + attempt)
        raise EmbeddingError("unreachable")

    def _embed_ollama(self, texts: list[str]) -> list[list[float]]:
        body = {
            "model": self.model,
            "input": texts,
            "truncate": True,
            "keep_alive": "30m",
            "options": {"num_ctx": self.settings.embed_num_ctx},
        }
        for attempt in range(4):
            try:
                response = self._client.post("/api/embed", json=body)
                if response.status_code == 404:
                    raise EmbeddingError(f"Model {self.model} not found in Ollama. Run: ollama pull {self.model}")
                response.raise_for_status()
                vectors = response.json().get("embeddings")
                if not vectors or len(vectors) != len(texts):
                    raise EmbeddingError(f"Ollama returned {len(vectors or [])} vectors for {len(texts)} texts")
                return vectors
            except (httpx.TransportError, httpx.HTTPStatusError) as error:
                if attempt == 3:
                    raise EmbeddingError(f"Embedding request failed: {error}") from error
                time.sleep(2 * (attempt + 1))
        raise EmbeddingError("unreachable")

    def _load(self, keys: list[str]) -> dict[str, list[float]]:
        out: dict[str, list[float]] = {}
        with self._lock:
            for start in range(0, len(keys), 500):
                part = keys[start : start + 500]
                marks = ",".join("?" * len(part))
                for key, blob in self._db.execute(f"SELECT key, vector FROM vectors WHERE key IN ({marks})", part):
                    out[key] = array("f", blob).tolist()
        return out

    def _store(self, vectors: dict[str, list[float]]) -> None:
        with self._lock:
            self._db.executemany(
                "INSERT OR REPLACE INTO vectors (key, vector) VALUES (?, ?)",
                [(k, array("f", v).tobytes()) for k, v in vectors.items()],
            )
            self._db.commit()


def cache_size(storage_dir: Path) -> int:
    path = storage_dir / "embedding_cache.sqlite"
    if not path.exists():
        return 0
    with sqlite3.connect(str(path)) as db:
        return db.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
