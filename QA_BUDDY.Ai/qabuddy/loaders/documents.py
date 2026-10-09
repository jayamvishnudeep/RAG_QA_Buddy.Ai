"""Prose documents (PDF, Markdown, Word, plain text), chunked along their headings.

A document becomes a tree of sections. A section that fits the token limit is
one chunk; a bigger one is split at its sub-sections, and only a single
oversized section is cut with overlap. Small neighbouring sections are joined
again, so the index holds no tiny fragments. Every chunk starts with the
document title and section path, which helps both search and the LLM.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..chunking import heading_level, pack_tagged, split_text
from ..models import Chunk
from ..sources import Chunking
from ..text import estimate_tokens, normalize, squash_spaces
from .base import LoadContext, Unsupported, page_label, read_text

MARKDOWN = {".md", ".mdc", ".markdown"}
TEXT = {".txt", ".text", ".rst"}
SUPPORTED = MARKDOWN | TEXT | {".pdf", ".docx"}

MINOR_HEADING = 9  # an unnumbered title line inside a numbered section


@dataclass
class Block:
    text: str
    level: int = 0  # 0 = body text, otherwise heading depth
    page: int | None = None


def load(ctx: LoadContext) -> list[Chunk]:
    suffix = ctx.path.suffix.lower()
    if suffix not in SUPPORTED:
        raise Unsupported(f"{suffix or 'no extension'} is not a document type")
    title, blocks, kind = parse(ctx.path)
    return chunk_blocks(ctx, blocks, title, ctx.source.chunking, kind=kind)


def parse(path: Path) -> tuple[str, list[Block], str]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        title, blocks = pdf_blocks(path)
        return title, blocks, "pdf"
    if suffix == ".docx":
        return _clean_title(path.stem), docx_blocks(path), "docx"
    text = read_text(path)
    blocks = markdown_blocks(text) if suffix in MARKDOWN else text_blocks(text)
    first_heading = next((b.text for b in blocks if b.level == 1), "")
    return first_heading or _clean_title(path.stem), blocks, "markdown" if suffix in MARKDOWN else "text"


def _clean_title(stem: str) -> str:
    return re.sub(r"\s*\(\d+\)$", "", stem.replace("_", " ")).strip()


# --- Parsers ------------------------------------------------------------------

_FENCE = re.compile(r"^\s*(```|~~~)")
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def markdown_blocks(text: str) -> list[Block]:
    blocks: list[Block] = []
    para: list[str] = []
    fence = ""

    def flush() -> None:
        if para:
            body = "\n".join(para).strip()
            if body:
                blocks.append(Block(body))
            para.clear()

    for line in normalize(text).split("\n"):
        if fence:
            para.append(line)
            if line.strip().startswith(fence):
                fence = ""
                flush()
            continue
        fence_match = _FENCE.match(line)
        if fence_match:
            flush()
            fence = fence_match.group(1)
            para.append(line)
            continue
        heading = _MD_HEADING.match(line)
        if heading:
            flush()
            blocks.append(Block(heading.group(2).strip(), level=len(heading.group(1))))
            continue
        if re.fullmatch(r"\s*(=+|-+)\s*", line) and len(para) == 1:  # setext heading
            blocks.append(Block(para[0].strip(), level=1 if "=" in line else 2))
            para.clear()
            continue
        if not line.strip():
            flush()
            continue
        para.append(line)
    flush()
    return blocks


def text_blocks(text: str) -> list[Block]:
    blocks = []
    for para in re.split(r"\n\s*\n", normalize(text)):
        para = para.strip()
        if not para:
            continue
        level = _heading_level(para) if "\n" not in para else 0
        blocks.append(Block(para, level))
    return blocks


_BULLET = re.compile(r"^[●•▪◦■➢►‣⁃]\s*")
_PAGE_NUMBER = re.compile(r"^(page\s*)?\d+(\s*(of|/)\s*\d+)?$", re.I)
_NUMBERED_HEADING = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+[A-Z(\"']")


def pdf_blocks(path: Path) -> tuple[str, list[Block]]:
    from pypdf import PdfReader

    reader = PdfReader(path)
    meta_title = (reader.metadata.title if reader.metadata else "") or ""
    pages: list[tuple[int, str]] = []
    for number, page in enumerate(reader.pages, 1):
        try:
            raw = page.extract_text(extraction_mode="layout") or ""
        except Exception:  # pypdf's layout mode fails on some exotic PDFs
            raw = (page.extract_text() or "").replace("\n \n", " ")
        pages.append((number, normalize(raw)))

    # Lines repeated on most pages are running headers and footers.
    seen = Counter(squash_spaces(line) for _, raw in pages for line in set(raw.split("\n")) if line.strip())
    repeated = {line for line, n in seen.items() if len(pages) >= 3 and n >= max(3, 0.6 * len(pages)) and len(line) < 120}

    blocks: list[Block] = []
    for number, raw in pages:
        lines = raw.split("\n")
        filled = [i for i, line in enumerate(lines) if line.strip()]
        edges = set(filled[:2] + filled[-2:])  # page numbers only ever sit at the top or bottom
        para: list[str] = []
        for i, line in enumerate(lines + [""]):
            stripped = line.strip()
            if stripped and (squash_spaces(stripped) in repeated or (i in edges and _PAGE_NUMBER.match(stripped))):
                continue
            if not stripped:
                if para:
                    text = re.sub(r"([a-z])-\n([a-z])", r"\1\2", "\n".join(para))
                    level = _heading_level(text) if len(para) == 1 else 0
                    blocks.append(Block(text, level, number))
                    para = []
                continue
            stripped = _BULLET.sub("- ", stripped)
            stripped = re.sub(r"\s{3,}", " | ", stripped)  # layout columns, mostly tables
            para.append(stripped)
    return _clean_title(meta_title if _real_title(meta_title) else path.stem), blocks


def _real_title(title: str) -> bool:
    """PDF metadata titles are often junk: 'qab onboarding.html', 'Microsoft Word - draft2.docx', 'Untitled'."""
    title = title.strip()
    return (
        len(title) >= 3
        and not re.search(r"\.[A-Za-z0-9]{2,5}$", title)
        and not title.lower().startswith(("microsoft word", "untitled", "document"))
    )


def docx_blocks(path: Path) -> list[Block]:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(path))
    blocks: list[Block] = []
    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph = Paragraph(child, doc)
            text = paragraph.text.strip()
            if not text:
                continue
            style = paragraph.style.name if paragraph.style is not None else ""
            level = 0
            if style == "Title":
                level = 1
            elif style.startswith("Heading") and style.split()[-1].isdigit():
                level = int(style.split()[-1])
            blocks.append(Block(normalize(text), level))
        elif tag == "tbl":
            rows = [" | ".join(cell.text.strip() for cell in row.cells) for row in Table(child, doc).rows]
            if rows:
                blocks.append(Block(normalize("\n".join(rows))))
    return blocks


def _heading_level(line: str) -> int:
    """Guess whether a single line is a heading: '4.1. Experimentation & Testing' or 'Stakeholders'."""
    s = line.strip()
    if not s or len(s) > 90 or " | " in s or s[0] in "-*" or s[-1] in ".,;:!?":
        return 0
    words = s.split()
    if _NUMBERED_HEADING.match(s) and len(words) <= 12:
        return heading_level(s)
    if len(words) <= 6 and s[0].isupper():
        capitalised = sum(1 for w in words if not w[0].isalpha() or w[0].isupper() or w.lower() in _SMALL_WORDS)
        if capitalised == len(words):
            return MINOR_HEADING
    return 0


_SMALL_WORDS = {"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to", "vs", "with", "&"}


# --- Section tree and chunking -------------------------------------------------


@dataclass
class _Section:
    heading: str = ""
    level: int = 0
    page: int | None = None
    body: list[Block] = field(default_factory=list)
    children: list["_Section"] = field(default_factory=list)


@dataclass
class _Group:
    path: list[str]
    text: str
    pages: list[int]
    headings: list[str] = field(default_factory=list)  # sections this group covers, for its citation


def chunk_blocks(
    ctx: LoadContext,
    blocks: list[Block],
    title: str,
    chunking: Chunking,
    kind: str,
    url: str = "",
    source_type: str = "",
) -> list[Chunk]:
    root = _tree(blocks)
    groups = _groups(root, [], chunking)
    chunks = []
    for group in groups:
        path = group.path
        if path and group.text.startswith(path[-1]):
            path = path[:-1]  # the chunk starts with its own heading
        header = " > ".join([title] + path)
        sections = [h for h in group.headings if h] or group.path[-1:]
        where = ""
        if sections:
            where = f"§ {sections[0][:50]}" + (f" to {sections[-1][:50]}" if len(sections) > 1 else "")
        location = " · ".join(p for p in (where, page_label(group.pages)) if p)
        chunks.append(
            ctx.chunk(
                f"{header}\n\n{group.text}",
                title=title,
                location=location,
                url=url,
                source_type=source_type,
                kind=kind,
                section=" > ".join(group.path),
                pages=sorted(set(p for p in group.pages if p)),
            )
        )
    return chunks


def _tree(blocks: list[Block]) -> _Section:
    root = _Section()
    stack = [root]
    for block in blocks:
        if block.level:
            section = _Section(block.text, block.level, block.page)
            while len(stack) > 1 and stack[-1].level >= block.level:
                stack.pop()
            stack[-1].children.append(section)
            stack.append(section)
        else:
            stack[-1].body.append(block)
    return root


def _units(section: _Section) -> list[tuple[str, int | None]]:
    units = [(section.heading, section.page)] if section.heading else []
    units += [(b.text, b.page) for b in section.body]
    for child in section.children:
        units += _units(child)
    return units


def _size(units: list[tuple[str, int | None]]) -> int:
    return sum(estimate_tokens(text) for text, _ in units)


def _groups(section: _Section, path: list[str], chunking: Chunking) -> list[_Group]:
    units = _units(section)
    if not units:
        return []
    if _size(units) <= chunking.max_tokens:
        return [_Group(path, "\n\n".join(t for t, _ in units), [p for _, p in units if p], [section.heading])]

    groups: list[_Group] = []
    own = ([(section.heading, section.page)] if section.heading else []) + [(b.text, b.page) for b in section.body]
    if own:
        pieces = []
        for text, page in own:  # break up single blocks that are too big on their own
            pieces += [(part, page) for part in split_text(text, chunking.max_tokens, chunking.overlap_tokens)]
        for text, pages in pack_tagged(pieces, chunking.max_tokens, chunking.overlap_tokens):
            groups.append(_Group(path, text, [p for p in pages if p], [section.heading]))
    for child in section.children:
        groups += _groups(child, path + [child.heading], chunking)
    return _merge_small(groups, chunking)


def _merge_small(groups: list[_Group], chunking: Chunking) -> list[_Group]:
    """Join neighbouring groups while they fit, so short sections don't become tiny chunks."""
    merged: list[_Group] = []
    for group in groups:
        if merged:
            last = merged[-1]
            combined = estimate_tokens(last.text) + estimate_tokens(group.text)
            small = min(estimate_tokens(last.text), estimate_tokens(group.text)) < chunking.min_tokens
            if combined <= chunking.max_tokens or (small and combined <= chunking.max_tokens * 1.2):
                merged[-1] = _Group(
                    _common(last.path, group.path),
                    f"{last.text}\n\n{group.text}",
                    last.pages + group.pages,
                    last.headings + group.headings,
                )
                continue
        merged.append(group)
    return merged


def _common(a: list[str], b: list[str]) -> list[str]:
    out = []
    for x, y in zip(a, b):
        if x != y:
            break
        out.append(x)
    return out
