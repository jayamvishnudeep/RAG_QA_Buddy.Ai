"""One loader per kind of source. Each turns one file into chunks."""

from __future__ import annotations

from typing import Callable

from ..models import Chunk
from . import code, diagrams, documents, jira, logs, tabular, transcripts
from .base import LoadContext, RepoInfo, Unsupported


def _figma(ctx: LoadContext) -> list[Chunk]:
    raise Unsupported("Figma ingestion is planned for Phase 2")


LOADERS: dict[str, Callable[[LoadContext], list[Chunk]]] = {
    "code": code.load,
    "tabular": tabular.load,
    "jira": jira.load,
    "documents": documents.load,
    "transcripts": transcripts.load,
    "diagrams": diagrams.load,
    "logs": logs.load,
    "figma": _figma,
}


def load_file(ctx: LoadContext) -> list[Chunk]:
    return LOADERS[ctx.source.loader](ctx)


__all__ = ["LOADERS", "LoadContext", "RepoInfo", "Unsupported", "load_file"]
