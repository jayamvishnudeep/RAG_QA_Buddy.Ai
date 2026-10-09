"""Lucid charts exported to text.

Lucid's "Export as CSV" gives one row per shape and per connector. Each page
becomes a readable outline: the shapes with their text, then the connections
as "A -> B (label)". Graph JSON (nodes and edges) is turned into the same
outline. Diagrams exported as text, Markdown, PDF or Word are chunked like
documents.
"""

from __future__ import annotations

import csv
import io
import json
from collections import defaultdict

from ..chunking import split_lines
from ..models import Chunk
from ..text import normalize, squash_spaces
from . import documents
from .base import LoadContext, Unsupported, read_text


def load(ctx: LoadContext) -> list[Chunk]:
    suffix = ctx.path.suffix.lower()
    title = f"Diagram: {ctx.path.stem.replace('_', ' ')}"
    if suffix == ".csv":
        return _lucid_csv(ctx, title)
    if suffix == ".json":
        return _graph_json(ctx)
    if suffix in documents.SUPPORTED:
        _, blocks, kind = documents.parse(ctx.path)
        return documents.chunk_blocks(ctx, blocks, title, ctx.source.chunking, kind=kind, source_type="diagram")
    raise Unsupported(f"{suffix} diagrams are not indexed (export to CSV or text; images are Phase 2)")


def _lucid_csv(ctx: LoadContext, title: str) -> list[Chunk]:
    rows = list(csv.DictReader(io.StringIO(read_text(ctx.path))))
    if not rows or "Name" not in rows[0]:
        raise Unsupported("CSV is not a Lucid shape-data export")
    text_columns = [c for c in rows[0] if c.lower().startswith("text area")]

    def text_of(row: dict) -> str:
        return squash_spaces(" / ".join(normalize(row.get(c) or "") for c in text_columns if (row.get(c) or "").strip()))

    pages: dict[str, str] = {}
    shapes: dict[str, dict] = {}
    lines_by_page: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for row in rows:
        kind = (row.get("Name") or "").strip()
        page = (row.get("Page ID") or "").strip()
        if kind == "Document":
            continue
        if kind == "Page":
            pages[row.get("Id", "")] = text_of(row) or f"Page {len(pages) + 1}"
            continue
        if kind == "Line" or row.get("Line Source") or row.get("Line Destination"):
            lines_by_page[page].append((row.get("Line Source", ""), row.get("Line Destination", ""), text_of(row)))
            continue
        shapes[row.get("Id", "")] = {"kind": kind, "text": text_of(row), "page": page}

    chunks: list[Chunk] = []
    page_ids = list(pages) or sorted({s["page"] for s in shapes.values()})
    for page_id in page_ids:
        page_title = pages.get(page_id, "Page")
        outline = [f"- {s['text']} ({s['kind']})" for s in shapes.values() if s["page"] == page_id and s["text"]]
        flows = []
        for src, dst, label in lines_by_page.get(page_id, []):
            a = shapes.get(src, {}).get("text") or src or "?"
            b = shapes.get(dst, {}).get("text") or dst or "?"
            flows.append(f"- {a} -> {b}" + (f" ({label})" if label else ""))
        body = []
        if outline:
            body += ["Shapes:"] + outline
        if flows:
            body += ["Connections:"] + flows
        if not body:
            continue
        chunks += _outline_chunks(ctx, title, page_title, body)
    return chunks


def _graph_json(ctx: LoadContext) -> list[Chunk]:
    """{"title", "nodes": [{"id", "label"}], "edges": [{"from", "to", "label"}]} and similar shapes."""
    try:
        data = json.loads(read_text(ctx.path))
    except json.JSONDecodeError as error:
        raise Unsupported(f"JSON could not be parsed: {error}") from None
    if not isinstance(data, dict):
        raise Unsupported("JSON diagram must be an object with nodes and edges")
    nodes = data.get("nodes") or data.get("shapes") or []
    edges = data.get("edges") or data.get("links") or data.get("lines") or data.get("connections") or []
    if not nodes and not edges:
        raise Unsupported("JSON has no nodes or edges")

    def label(node: dict) -> str:
        return squash_spaces(str(node.get("label") or node.get("text") or node.get("name") or node.get("id") or ""))

    names = {str(n.get("id")): label(n) for n in nodes if isinstance(n, dict)}
    body = ["Shapes:"] + [f"- {name}" for name in names.values() if name]
    if edges:
        body.append("Connections:")
        for e in edges:
            if not isinstance(e, dict):
                continue
            a = names.get(str(e.get("from", e.get("source", ""))), str(e.get("from", e.get("source", "?"))))
            b = names.get(str(e.get("to", e.get("target", ""))), str(e.get("to", e.get("target", "?"))))
            text = squash_spaces(str(e.get("label") or e.get("text") or ""))
            body.append(f"- {a} -> {b}" + (f" ({text})" if text else ""))
    title = f"Diagram: {data.get('title') or ctx.path.stem.replace('_', ' ')}"
    return _outline_chunks(ctx, title, data.get("page") or "", body)


def _outline_chunks(ctx: LoadContext, title: str, page_title: str, body: list[str]) -> list[Chunk]:
    chunking = ctx.source.chunking
    heading = f"{title} > {page_title}" if page_title else title
    return [
        ctx.chunk(
            f"{heading}\n\n{window.text}",
            title=title,
            location=page_title or f"lines {window.first_line}-{window.last_line}",
            source_type="diagram",
            kind="lucid",
            page=page_title,
        )
        for window in split_lines(body, chunking.max_tokens, chunking.overlap_tokens)
    ]
