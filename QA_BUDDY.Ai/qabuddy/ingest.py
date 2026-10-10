"""Ingestion: scan the source folders, chunk, embed and index - incrementally.

Each file is fingerprinted (content, loader version, chunk sizes, embedding
model). Unchanged files are skipped, changed files are re-indexed, and files
that disappeared are removed from the index. That makes re-running ingestion
cheap and safe, which the hourly auto-ingestion (auto_ingest.py) relies on.
A lock file keeps two runs from overlapping.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from .embeddings import Embedder
from .loaders import LoadContext, RepoInfo, Unsupported, load_file
from .models import Chunk
from .settings import Settings
from .sources import Catalog, Source, load_catalog
from .sparse import encode_document
from .store import Store
from .text import estimate_tokens

LOADER_VERSION = "4"
WRITE_BATCH = 192  # chunks embedded and written per round; progress is saved after each


class IngestBusy(RuntimeError):
    """Another ingestion (the app's hourly run or a manual one) holds the lock."""


@contextmanager
def ingest_lock(settings: Settings) -> Iterator[None]:
    """One ingestion at a time, across processes. The OS releases the lock if the process dies."""
    path = settings.storage_dir / "ingest.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as handle:
        try:
            _lock(handle)
        except OSError:
            raise IngestBusy("Another ingestion is running (storage/ingest.lock). Try again when it has finished.") from None
        try:
            yield
        finally:
            _unlock(handle)


if os.name == "nt":
    import msvcrt

    def _lock(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@dataclass
class SourceReport:
    label: str
    folder: str
    files: int = 0
    indexed: int = 0
    unchanged: int = 0
    skipped: int = 0
    errors: int = 0
    chunks: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass
class IngestReport:
    started: str
    finished: str = ""
    seconds: float = 0.0
    sources: dict[str, SourceReport] = field(default_factory=dict)
    removed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    total_chunks: int = 0


class Manifest:
    """doc_id -> fingerprint of what is currently indexed. Lives next to the index."""

    def __init__(self, path: Path):
        self.path = path
        self.entries: dict[str, dict] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.entries, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)


def _fingerprint(path: Path, source: Source, catalog: Catalog, settings: Settings) -> str:
    digest = hashlib.sha256(path.read_bytes())
    chunking = sorted(catalog.chunking.items())
    digest.update(f"|{LOADER_VERSION}|{source.loader}|{chunking}|{settings.embed_model}".encode())
    return digest.hexdigest()


def run_ingest(
    settings: Settings,
    only: list[str] | None = None,
    rebuild: bool = False,
    log: Callable[[str], None] = print,
    *,
    lock: bool = True,
) -> IngestReport:
    """Index what changed. Pass lock=False only when the caller already holds ingest_lock."""
    if lock:
        with ingest_lock(settings):
            return _ingest(settings, only, rebuild, log)
    return _ingest(settings, only, rebuild, log)


def _ingest(settings: Settings, only: list[str] | None, rebuild: bool, log: Callable[[str], None]) -> IngestReport:
    started = time.time()
    report = IngestReport(started=dt.datetime.now().isoformat(timespec="seconds"))
    catalog = load_catalog(settings)
    store, embedder = Store(settings), Embedder(settings)

    for name, health in (("Qdrant", store.health()), ("Embeddings", embedder.health())):
        if not health["ok"]:
            raise RuntimeError(f"{name}: {health['error']}")

    manifest = Manifest(settings.storage_dir / "manifest.json")
    if rebuild:
        log("Rebuilding: dropping the collection")
        store.drop()
        manifest.entries.clear()
    dimension = embedder.dimension()
    if store.ensure(dimension):
        log(f"Created collection '{settings.collection}' ({dimension}-dim dense + BM25 sparse)")
        manifest.entries.clear()

    selected = [s for s in catalog.sources if not only or s.key in only]
    unknown = set(only or []) - {s.key for s in catalog.sources}
    if unknown:
        raise ValueError(f"Unknown source(s): {', '.join(sorted(unknown))}")

    pending: list[tuple[Source, str, list[Chunk], str]] = []
    seen: set[str] = set()
    for source in selected:
        rep = report.sources[source.key] = SourceReport(source.label, _display(source.folder, settings))
        if not source.active:
            rep.notes.append(f"Phase {source.phase}: not ingested yet")
            log(f"- {source.label}: skipped (Phase {source.phase})")
            continue
        if not source.folder.is_dir():
            rep.notes.append("folder not found")
            log(f"- {source.label}: folder not found ({rep.folder})")
            continue
        repo = RepoInfo.detect(source.folder) if source.loader == "code" else None
        for path, doc_id in catalog.files(source, settings.data_dir):
            seen.add(doc_id)
            rep.files += 1
            fingerprint = _fingerprint(path, source, catalog, settings)
            entry = manifest.entries.get(doc_id)
            if entry and entry.get("fingerprint") == fingerprint:
                if entry.get("skipped"):
                    rep.skipped += 1
                else:
                    rep.unchanged += 1
                    rep.chunks += entry.get("chunks", 0)
                continue
            rel = _relative(path, source)
            ctx = LoadContext(source, path, doc_id, rel, settings, repo, catalog.chunking)
            try:
                chunks = load_file(ctx)
            except Unsupported as reason:
                rep.skipped += 1
                if entry and entry.get("chunks"):
                    store.delete_docs([doc_id])
                manifest.entries[doc_id] = {"fingerprint": fingerprint, "source": source.key, "chunks": 0, "skipped": str(reason)}
                continue
            except Exception as error:  # one bad file must not stop the run
                rep.errors += 1
                report.errors.append(f"{doc_id}: {type(error).__name__}: {error}")
                log(f"  ! {doc_id}: {type(error).__name__}: {error}")
                continue
            pending.append((source, doc_id, chunks, fingerprint))
        log(f"- {source.label}: {rep.files} files, {rep.unchanged} unchanged")
        _warn_untracked(source, catalog, settings, rep, log)

    # Files that were indexed before but are gone now.
    selected_keys = {s.key for s in selected if s.active}
    removed = [d for d, e in manifest.entries.items() if e.get("source") in selected_keys and d not in seen]
    if removed:
        store.delete_docs(removed)
        for doc_id in removed:
            del manifest.entries[doc_id]
        manifest.save()
        report.removed = removed
        log(f"Removed {len(removed)} deleted file(s) from the index")

    total = sum(len(chunks) for _, _, chunks, _ in pending)
    if total:
        log(f"Embedding and indexing {total} chunks from {len(pending)} changed file(s) with {settings.embed_model}")
    done = 0
    batch: list[tuple[Source, str, list[Chunk], str]] = []
    for item in pending + [None]:  # type: ignore[list-item]
        if item is not None:
            batch.append(item)
        size = sum(len(c) for _, _, c, _ in batch)
        if batch and (item is None or size >= WRITE_BATCH):
            _write(batch, store, embedder, manifest, report, done, total, log)
            done += size
            batch = []

    report.finished = dt.datetime.now().isoformat(timespec="seconds")
    report.seconds = round(time.time() - started, 1)
    report.total_chunks = store.count()
    (settings.storage_dir / "last_ingest.json").write_text(json.dumps(asdict(report), indent=1), encoding="utf-8")
    return report


def _write(batch, store: Store, embedder: Embedder, manifest: Manifest, report: IngestReport, done: int, total: int, log) -> None:
    chunks = [c for _, _, cs, _ in batch for c in cs]
    texts = [c.text for c in chunks]
    started = time.time()

    def progress(n: int, of: int) -> None:
        if of and (n == of or n % 32 == 0):
            elapsed = time.time() - started
            log(f"  embedded {done + n}/{total} chunks" + (f" ({elapsed / max(n, 1):.2f}s each)" if n else ""))

    dense = embedder.embed_documents(texts, progress)
    sparse = [encode_document(t) for t in texts]
    now = dt.datetime.now().isoformat(timespec="seconds")
    offset = 0
    for source, doc_id, doc_chunks, fingerprint in batch:
        n = len(doc_chunks)
        store.delete_docs([doc_id])
        if n:
            payloads = []
            for i, chunk in enumerate(doc_chunks):
                payload = chunk.payload()
                payload.update(chunk=i, chunks=n, tokens=estimate_tokens(chunk.text), indexed_at=now)
                payloads.append(payload)
            store.upsert([c.point_id(i) for i, c in enumerate(doc_chunks)], dense[offset : offset + n], sparse[offset : offset + n], payloads)
        offset += n
        manifest.entries[doc_id] = {"fingerprint": fingerprint, "source": source.key, "chunks": n, "indexed_at": now}
        rep = report.sources[source.key]
        rep.indexed += 1
        rep.chunks += n
    manifest.save()


def _relative(path: Path, source: Source) -> str:
    try:
        return path.relative_to(source.folder).as_posix()
    except ValueError:  # extra folders, e.g. Jira MCP snapshots
        for folder, prefix in source.extra_dirs:
            try:
                return f"{prefix}/{path.relative_to(folder).as_posix()}"
            except ValueError:
                continue
    return path.name


def _display(folder: Path, settings: Settings) -> str:
    try:
        return folder.relative_to(settings.data_dir).as_posix()
    except ValueError:
        return str(folder)


def _warn_untracked(source: Source, catalog: Catalog, settings: Settings, rep: SourceReport, log) -> None:
    """A repository dropped next to the configured ones is easy to forget in sources.yaml."""
    if source.loader != "code":
        return
    parent = source.folder.parent
    configured = {s.folder for s in catalog.sources if s.loader == "code"}
    if source.folder != max(configured, key=str):  # report once
        return
    for child in sorted(parent.iterdir()) if parent.is_dir() else []:
        if child.is_dir() and child not in configured and not child.name.startswith("."):
            note = f"'{child.name}' in {parent.name} is not in config/sources.yaml, so it is not indexed"
            rep.notes.append(note)
            log(f"  note: {note}")
