"""Generic splitters shared by the loaders.

Loaders split on natural boundaries first (rows, functions, sections, speaker
turns, build stages). These helpers only handle what is still too big: they
pack smaller units into chunks up to a token limit, carrying a small overlap
from the end of one chunk into the next.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TypeVar

from .text import PROSE_CHARS_PER_TOKEN, estimate_tokens

T = TypeVar("T")

_SEPARATORS = ["\n\n", "\n", ". ", "; ", ", ", " "]


def split_text(
    text: str,
    max_tokens: int,
    overlap_tokens: int = 0,
    chars_per_token: float = PROSE_CHARS_PER_TOKEN,
) -> list[str]:
    """Split prose into chunks of at most max_tokens, preferring paragraph and sentence breaks."""
    text = text.strip()
    if not text:
        return []
    if estimate_tokens(text, chars_per_token) <= max_tokens:
        return [text]
    units = _units(text, max_tokens, chars_per_token, _SEPARATORS)
    return pack(units, max_tokens, overlap_tokens, chars_per_token)


def _units(text: str, max_tokens: int, cpt: float, separators: list[str]) -> list[str]:
    """Break text into pieces that each fit max_tokens, keeping separators attached."""
    if estimate_tokens(text, cpt) <= max_tokens:
        return [text]
    if not separators:
        size = int(max_tokens * cpt)
        return [text[i : i + size] for i in range(0, len(text), size)]
    sep, rest = separators[0], separators[1:]
    parts = text.split(sep)
    if len(parts) == 1:
        return _units(text, max_tokens, cpt, rest)
    pieces = [p + sep for p in parts[:-1]] + [parts[-1]]
    out: list[str] = []
    for piece in pieces:
        if not piece:
            continue
        out.extend(_units(piece, max_tokens, cpt, rest))
    return out


def pack(units: list[str], max_tokens: int, overlap_tokens: int = 0, chars_per_token: float = PROSE_CHARS_PER_TOKEN) -> list[str]:
    """Greedily join units into chunks; start each new chunk with the previous chunk's tail."""
    packed = pack_tagged([(u, None) for u in units], max_tokens, overlap_tokens, chars_per_token, joiner="")
    return [text for text, _ in packed]


def pack_tagged(
    units: list[tuple[str, T]],
    max_tokens: int,
    overlap_tokens: int = 0,
    chars_per_token: float = PROSE_CHARS_PER_TOKEN,
    joiner: str = "\n\n",
) -> list[tuple[str, list[T]]]:
    """Like pack(), but each unit carries a tag (a page number, a timestamp) that is kept per chunk."""
    chunks: list[tuple[str, list[T]]] = []
    current: list[tuple[str, T]] = []
    size = 0
    for unit in units:
        tokens = estimate_tokens(unit[0], chars_per_token)
        if current and size + tokens > max_tokens:
            chunks.append(_join(current, joiner))
            current, size = _tail(current, overlap_tokens, chars_per_token)
            if size + tokens > max_tokens:  # the overlap alone would overflow: drop it
                current, size = [], 0
        current.append(unit)
        size += tokens
    if current:
        chunks.append(_join(current, joiner))
    return [(text, tags) for text, tags in chunks if text]


def _join(units: list[tuple[str, T]], joiner: str) -> tuple[str, list[T]]:
    return joiner.join(u[0] for u in units).strip(), [u[1] for u in units]


def _tail(units: list[tuple[str, T]], overlap_tokens: int, cpt: float) -> tuple[list[tuple[str, T]], int]:
    if overlap_tokens <= 0:
        return [], 0
    tail: list[tuple[str, T]] = []
    size = 0
    for unit in reversed(units):
        tokens = estimate_tokens(unit[0], cpt)
        if size + tokens > overlap_tokens:
            break
        tail.insert(0, unit)
        size += tokens
    return tail, size


@dataclass
class LineWindow:
    first_line: int  # 1-based, inclusive
    last_line: int
    text: str


def split_lines(
    lines: list[str],
    max_tokens: int,
    overlap_tokens: int = 0,
    chars_per_token: float = PROSE_CHARS_PER_TOKEN,
    first_line: int = 1,
) -> list[LineWindow]:
    """Split a list of lines into windows, keeping line numbers for citations."""
    windows: list[LineWindow] = []
    start = 0
    n = len(lines)
    budget_chars = max_tokens * chars_per_token
    while start < n:
        end = start
        chars = 0
        while end < n and (end == start or chars + len(lines[end]) + 1 <= budget_chars):
            chars += len(lines[end]) + 1
            end += 1
        text = "\n".join(lines[start:end])
        if estimate_tokens(text, chars_per_token) > max_tokens:  # one huge line
            text = text[: int(budget_chars)]
        windows.append(LineWindow(first_line + start, first_line + end - 1, text))
        if end >= n:
            break
        # Step back far enough to repeat ~overlap_tokens of context.
        back = end
        carried = 0
        while back > start + 1 and carried + len(lines[back - 1]) + 1 <= overlap_tokens * chars_per_token:
            carried += len(lines[back - 1]) + 1
            back -= 1
        start = back if overlap_tokens > 0 else end
    return windows


_HEADING_NUMBER = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s+\S")


def heading_level(numbered_heading: str) -> int:
    """'4.1. Experimentation' -> 2; plain text -> 0."""
    match = _HEADING_NUMBER.match(numbered_heading)
    return match.group(1).count(".") + 1 if match else 0
