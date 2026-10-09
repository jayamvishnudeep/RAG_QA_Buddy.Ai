"""Test cases from CSV and Excel: one row is one chunk.

A row is the natural unit of a test case, so rows are never merged or
overlapped. Before a row is written out, columns that repeat each other are
dropped: duplicated columns, a "Description" that already contains every other
field, and long notes that are identical on every row. That keeps each test
case to roughly 150-300 tokens.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re

from ..chunking import split_lines, split_text
from ..models import Chunk
from ..text import MASK, estimate_tokens, mask_secrets, normalize
from .base import LoadContext, Unsupported, read_text

ID_ALIASES = ["test case id", "testcase id", "tc id", "test id", "scenario tid", "scenario id", "case id", "issue key", "key", "id"]
TITLE_ALIASES = [
    "summary", "title", "test case title", "testcase title", "test case name", "test name", "testcase description",
    "test case description", "test scenario", "scenario", "description", "name",
]
PRIORITY_ALIASES = ["priority", "severity"]
STATUS_ALIASES = ["status", "execution status", "result", "test status"]

_STEP_SEPARATOR = re.compile(r"\s+\|\s+(?=\d+[.)]\s)")  # "1. Open | 2. Click" -> one step per line
_SECRET_COLUMN = re.compile(r"(?i)\b(pass(word|wd)?|pwd|secret|token|api[ _-]?key|credentials?)\b")


def load(ctx: LoadContext, source_type: str = "test_case") -> list[Chunk]:
    suffix = ctx.path.suffix.lower()
    if suffix in (".csv", ".tsv"):
        tables = [("", _read_csv(ctx))]
    elif suffix in (".xlsx", ".xlsm"):
        tables = _read_xlsx(ctx)
    else:
        raise Unsupported(f"{suffix} is not a spreadsheet")
    chunks: list[Chunk] = []
    for sheet, rows in tables:
        chunks += _table_chunks(ctx, sheet, rows, source_type)
    return chunks


def _read_csv(ctx: LoadContext) -> list[list[str]]:
    text = read_text(ctx.path)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [list(r) for r in csv.reader(io.StringIO(text), dialect)]


def _read_xlsx(ctx: LoadContext) -> list[tuple[str, list[list[str]]]]:
    from openpyxl import load_workbook

    workbook = load_workbook(ctx.path, read_only=True, data_only=True)
    try:
        return [(ws.title, [[_cell(v) for v in row] for row in ws.iter_rows(values_only=True)]) for ws in workbook.worksheets]
    finally:
        workbook.close()


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return str(value)


def _find(header: list[str], aliases: list[str], exclude: int | None = None) -> int | None:
    lowered = [re.sub(r"\s+", " ", h.lower().strip()) for h in header]
    for alias in aliases:
        for i, h in enumerate(lowered):
            if h == alias and i != exclude:
                return i
    return None


def _table_chunks(ctx: LoadContext, sheet: str, rows: list[list[str]], source_type: str) -> list[Chunk]:
    rows = [r for r in rows if any(str(c).strip() for c in r)]
    if len(rows) < 2:
        return []
    header = [normalize(h) or f"Column {i + 1}" for i, h in enumerate(rows[0])]
    data = [[normalize(str(c)) for c in r] + [""] * (len(header) - len(r)) for r in rows[1:]]
    for i, name in enumerate(header):  # a "password" column is a secret whatever its values look like
        if _SECRET_COLUMN.search(name):
            for row in data:
                if row[i]:
                    row[i] = MASK
    if source_type != "test_case":
        return _data_table_chunks(ctx, sheet, header, data)

    id_col = _find(header, ID_ALIASES)
    title_col = _find(header, TITLE_ALIASES, exclude=id_col)
    priority_col = _find(header, PRIORITY_ALIASES)
    status_col = _find(header, STATUS_ALIASES)

    # Long values that are the same on every row (import notes, boilerplate) carry no per-row information.
    constant = {
        i for i in range(len(header))
        if len(data) >= 20 and len({r[i] for r in data}) == 1 and len(data[0][i]) > 80
    }

    chunking = ctx.source.chunking
    chunks: list[Chunk] = []
    for number, row in enumerate(data, 1):
        test_id = row[id_col] if id_col is not None else ""
        title = row[title_col] if title_col is not None else ""
        if test_id and title.startswith(f"[{test_id}]"):
            title = title[len(test_id) + 2 :].strip()

        fields: list[tuple[str, str]] = []
        for i, name in enumerate(header):
            if i in constant or i in (id_col, title_col):
                continue
            value = _STEP_SEPARATOR.sub("\n", row[i]).strip()
            if not value or value in (v for _, v in fields):  # duplicated column
                continue
            fields.append((name, value))
        fields = [(n, v) for n, v in fields if not _repeats_others(v, [o for m, o in fields if m != n])]

        where = f"{ctx.rel_path}" + (f" · sheet {sheet}" if sheet else "") + f" · row {number}"
        first = f"Test case {test_id}: {title}" if test_id else f"Test case: {title or f'row {number}'}"
        head = f"{first}\nSource: {ctx.source.label} · {where}"
        body = mask_secrets("\n".join(f"{n}: {v}" if "\n" not in v else f"{n}:\n{v}" for n, v in fields))

        budget = chunking.max_tokens - estimate_tokens(head)
        parts = split_text(body, budget, chunking.overlap_tokens) if estimate_tokens(body) > budget else [body]
        for k, part in enumerate(parts, 1):
            suffix = f" (part {k}/{len(parts)})" if len(parts) > 1 else ""
            chunks.append(
                ctx.chunk(
                    f"{head}{suffix}\n{part}",
                    title=test_id or title[:60] or f"Row {number}",
                    location=(f"sheet {sheet} · " if sheet else "") + f"row {number}",
                    source_type=source_type,
                    test_id=test_id,
                    name=title,
                    priority=row[priority_col] if priority_col is not None else "",
                    status=row[status_col] if status_col is not None else "",
                    row=number,
                    sheet=sheet,
                )
            )
    return chunks


def _data_table_chunks(ctx: LoadContext, sheet: str, header: list[str], data: list[list[str]]) -> list[Chunk]:
    """Test data inside a repository (DDT sheets) is a table, not a list of test cases: index it as one."""
    chunking = ctx.source.chunking
    title = ctx.path.name + (f" · {sheet}" if sheet else "")
    head = f"{ctx.source.label} test data · {ctx.rel_path}" + (f" · sheet {sheet}" if sheet else "") + f"\n{' | '.join(header)}"
    lines = [" | ".join(row) for row in data]
    chunks = []
    for window in split_lines(lines, chunking.max_tokens - estimate_tokens(head), 0):
        first, last = window.first_line, window.last_line
        chunks.append(
            ctx.chunk(
                f"{head}\n{mask_secrets(window.text)}",
                title=title,
                location=f"rows {first}-{last}",
                source_type="test_data",
                columns=header,
                sheet=sheet,
            )
        )
    return chunks


def _repeats_others(value: str, others: list[str]) -> bool:
    """True for a composite field that just restates three or more of the row's other fields."""
    if len(value) < 120:
        return False
    haystack = _squash(value)
    contained = sum(1 for other in others if len(other) >= 15 and _squash(other) in haystack)
    return contained >= 3


def _squash(text: str) -> str:
    """Compare text regardless of line breaks and ' | ' step separators."""
    return re.sub(r"[\s|]+", " ", text).strip().lower()
