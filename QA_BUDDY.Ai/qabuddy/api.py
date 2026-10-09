"""HTTP API and web app.

  GET  /api/health          Qdrant, embeddings and LLM status
  GET  /api/config          sources (with chunk counts), modes, model info
  POST /api/chat            streamed answer (server-sent events)
  POST /api/search          retrieval only
  GET  /api/chunks/{id}     one indexed chunk
  /                         the chat UI (web/)

Set QABUDDY_USERNAME and QABUDDY_PASSWORD to require a login (HTTP Basic).
"""

from __future__ import annotations

import base64
import binascii
import json
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, prompts
from .answer import answer_stream, source_item
from .embeddings import REQUEST_TOKEN, Embedder
from .llm import LLM
from .retrieval import Retriever
from .settings import APP_DIR, get_settings
from .snapshot import open_store
from .sources import load_catalog

settings = get_settings()
app = FastAPI(title="QABuddy.ai", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json")


@lru_cache
def services() -> tuple:
    store = open_store(settings)
    embedder = Embedder(settings)
    return store, embedder, Retriever(settings, store, embedder), LLM(settings)


# --- Optional login ---------------------------------------------------------------------


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    # Vercel signs each request with an OIDC token; the AI Gateway accepts it for embeddings.
    oidc = request.headers.get("x-vercel-oidc-token")
    if oidc:
        REQUEST_TOKEN.set(oidc)
    if not (settings.auth_user and settings.auth_password) or request.url.path == "/api/health":
        return await call_next(request)
    header = request.headers.get("authorization", "")
    if header.lower().startswith("basic "):
        try:
            user, _, password = base64.b64decode(header[6:]).decode("utf-8").partition(":")
        except (binascii.Error, UnicodeDecodeError):
            user = password = ""
        if secrets.compare_digest(user, settings.auth_user) and secrets.compare_digest(password, settings.auth_password):
            return await call_next(request)
    return Response("Login required", status_code=401, headers={"WWW-Authenticate": 'Basic realm="QABuddy"'})


# --- Models -----------------------------------------------------------------------------


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=20000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    history: list[Turn] = Field(default_factory=list, max_length=20)
    mode: str = "ask"
    sources: list[str] | None = None
    top_k: int | None = Field(default=None, ge=1, le=20)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    sources: list[str] | None = None
    top_k: int = Field(default=8, ge=1, le=50)
    mode: Literal["hybrid", "semantic", "keyword"] = "hybrid"


# --- Routes -----------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict:
    store, embedder, _, llm = services()
    qdrant, embeddings = store.health(), embedder.health()
    ok = qdrant["ok"] and embeddings["ok"]
    return {
        "status": "ok" if ok else "degraded",
        "version": __version__,
        "qdrant": qdrant,
        "embeddings": embeddings,
        "llm": {"configured": llm.configured, "provider": llm.provider, "model": llm.model},
    }


@app.get("/api/config")
def config() -> dict:
    store, _, _, llm = services()
    catalog = load_catalog(settings)
    try:
        counts = store.counts_by("source")
    except Exception:  # Qdrant down: the UI still loads and shows the error from /api/health
        counts = {}
    last_ingest = {}
    report = settings.storage_dir / "last_ingest.json"
    if report.exists():
        data = json.loads(report.read_text(encoding="utf-8"))
        last_ingest = {"finished": data.get("finished"), "seconds": data.get("seconds"), "total_chunks": data.get("total_chunks")}
    elif getattr(store, "meta", None):  # a snapshot knows when its index was built
        last_ingest = {"finished": store.meta.get("last_ingest") or store.meta.get("exported"), "total_chunks": store.count()}
    return {
        "version": __version__,
        "sources": [
            {
                "key": s.key,
                "label": s.label,
                "folder": _relative_folder(s.folder),
                "loader": s.loader,
                "phase": s.phase,
                "chunks": counts.get(s.key, 0),
            }
            for s in catalog.sources
        ],
        "modes": [
            {"key": m.key, "label": m.label, "hint": m.hint, "sources": list(m.sources), "examples": list(m.examples)}
            for m in prompts.MODES.values()
        ],
        "llm": {"configured": llm.configured, "provider": llm.provider, "model": llm.model},
        "embedding_model": settings.embed_model,
        "top_k": settings.top_k,
        "jira_mcp": settings.jira_mcp_configured,
        "last_ingest": last_ingest,
    }


@app.post("/api/chat")
def chat(body: ChatRequest) -> StreamingResponse:
    _, _, retriever, llm = services()

    def events():
        stream = answer_stream(
            settings,
            body.question.strip(),
            history=[t.model_dump() for t in body.history],
            mode=body.mode,
            sources=body.sources or None,
            top_k=body.top_k,
            retriever=retriever,
            llm=llm,
        )
        try:
            for event, data in stream:
                yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        except Exception as error:  # never leave the browser hanging on a broken stream
            yield f"event: error\ndata: {json.dumps({'message': f'{type(error).__name__}: {error}'})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/search")
def search(body: SearchRequest) -> dict:
    _, _, retriever, _ = services()
    labels = {s.key: s.label for s in load_catalog(settings).sources}
    hits = retriever.search(body.query, sources=body.sources or None, top_k=body.top_k, mode=body.mode)
    return {"query": body.query, "results": [source_item(n, h, labels) for n, h in enumerate(hits, 1)]}


@app.get("/api/chunks/{point_id}")
def chunk(point_id: str) -> dict:
    store = services()[0]
    hit = store.get(point_id)
    if not hit:
        raise HTTPException(404, "No such chunk")
    return {"id": hit.id, **hit.payload}


@app.exception_handler(Exception)
async def unexpected(request: Request, error: Exception) -> JSONResponse:
    return JSONResponse({"detail": f"{type(error).__name__}: {error}"}, status_code=500)


def _relative_folder(folder: Path) -> str:
    try:
        return folder.relative_to(settings.data_dir).as_posix()
    except ValueError:
        return str(folder)


# On Vercel the UI is served from the CDN (public/), so the function only answers /api/*.
if (APP_DIR / "web").is_dir():
    app.mount("/", StaticFiles(directory=APP_DIR / "web", html=True), name="web")
