"""Framework repositories: code chunked along the syntax tree, docs by section.

Java and TypeScript/JavaScript files are parsed with tree-sitter. Each chunk is
a run of whole declarations (methods, classes, `test(...)` blocks) that fits
the token limit; only a single declaration that is too big on its own is cut
by lines, with overlap. Every chunk is prefixed with its file, line range and
enclosing scope (class, describe block), so a method still makes sense alone.
"""

from __future__ import annotations

import bisect
import textwrap
from dataclasses import dataclass, field

from ..chunking import split_lines
from ..models import Chunk
from ..text import CODE_CHARS_PER_TOKEN, is_config_file, mask_secrets, normalize, squash_spaces
from . import documents, tabular
from .base import LoadContext, Unsupported, read_text

AST_LANGUAGES = {
    ".java": "java",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
}
LANGUAGE_NAMES = {"java": "Java", "typescript": "TypeScript", "tsx": "TypeScript (TSX)", "javascript": "JavaScript"}

TEXT_CODE = {
    ".xml", ".properties", ".json", ".yml", ".yaml", ".gradle", ".kts", ".feature", ".sh", ".ps1",
    ".bat", ".cmd", ".toml", ".ini", ".cfg", ".conf", ".sql", ".py", ".kt", ".cs", ".go", ".rb",
    ".groovy", ".html", ".css", ".scss", ".example",
}
TEXT_CODE_NAMES = {"Dockerfile", "Jenkinsfile", "Makefile", ".env.example", ".editorconfig", ".nvmrc"}
DOC_NAMES = {".cursorrules", ".windsurfrules", ".augment-guidelines", "LICENSE", "CODEOWNERS"}
DATA_FILES = {".csv", ".xlsx", ".xlsm"}
SKIP_NAMES = {".gitignore", ".gitattributes", ".npmrc", ".prettierignore", ".eslintignore"}

_COMMENT_TYPES = ("comment",)
_CONTAINERS = {
    "class_declaration", "interface_declaration", "enum_declaration", "record_declaration",
    "annotation_type_declaration", "abstract_class_declaration", "method_declaration",
    "constructor_declaration", "function_declaration", "method_definition", "generator_function_declaration",
}
_TEST_CALLS = {
    "test", "it", "describe", "context", "test.describe", "test.step", "test.only", "test.skip",
    "test.fixme", "test.describe.serial", "test.describe.parallel", "test.describe.only",
    "test.beforeEach", "test.afterEach", "test.beforeAll", "test.afterAll",
    "beforeEach", "afterEach", "beforeAll", "afterAll",
}

_PARSERS: dict = {}


def load(ctx: LoadContext) -> list[Chunk]:
    path = ctx.path
    name, suffix = path.name, path.suffix.lower()
    if name in SKIP_NAMES:
        raise Unsupported("git/tool metadata")
    if suffix in DATA_FILES:
        return tabular.load(ctx, source_type="test_data")
    if suffix in documents.MARKDOWN or name in DOC_NAMES or suffix in documents.TEXT:
        return _repo_doc(ctx)
    if suffix in AST_LANGUAGES:
        return _ast_chunks(ctx, AST_LANGUAGES[suffix])
    if suffix in TEXT_CODE or name in TEXT_CODE_NAMES:
        return _text_chunks(ctx)
    raise Unsupported(f"{suffix or name} is not indexed")


# --- Docs inside a repository ------------------------------------------------------


def _repo_doc(ctx: LoadContext) -> list[Chunk]:
    text = mask_secrets(read_text(ctx.path))
    blocks = documents.markdown_blocks(text)
    title = f"{_label(ctx)} · {ctx.rel_path}"
    url = ctx.repo.file_url(ctx.rel_path) if ctx.repo else ""
    return documents.chunk_blocks(
        ctx, blocks, title, ctx.chunking_for("documents"), kind="doc", url=url, source_type="code"
    )


# --- Plain-text code and config files ------------------------------------------------


def _text_chunks(ctx: LoadContext) -> list[Chunk]:
    text = normalize(read_text(ctx.path))
    text = mask_secrets(text, config_file=is_config_file(ctx.path))
    if not text.strip():
        return []
    chunking = ctx.source.chunking
    windows = split_lines(text.split("\n"), chunking.max_tokens, chunking.overlap_tokens, CODE_CHARS_PER_TOKEN)
    kind = "config" if is_config_file(ctx.path) or ctx.path.suffix.lower() in {".xml", ".json", ".gradle"} else "code"
    return [
        _code_chunk(ctx, w.text, w.first_line, w.last_line, ctx.path.suffix.lstrip(".") or ctx.path.name, [], [], kind)
        for w in windows
    ]


# --- Syntax-aware chunking ----------------------------------------------------------


@dataclass
class _Range:
    start: int  # byte offsets into the file
    end: int
    nodes: list = field(default_factory=list)
    split: bool = False  # produced by cutting one node by lines


def _parser(language: str):
    if language not in _PARSERS:
        import tree_sitter as ts

        if language == "java":
            import tree_sitter_java as grammar

            lang = ts.Language(grammar.language())
        elif language in ("typescript", "tsx"):
            import tree_sitter_typescript as grammar

            lang = ts.Language(grammar.language_typescript() if language == "typescript" else grammar.language_tsx())
        else:
            import tree_sitter_javascript as grammar

            lang = ts.Language(grammar.language())
        _PARSERS[language] = ts.Parser(lang)
    return _PARSERS[language]


def _ast_chunks(ctx: LoadContext, language: str) -> list[Chunk]:
    text = normalize(read_text(ctx.path))
    if not text.strip():
        return []
    src = text.encode("utf-8")
    root = _parser(language).parse(src).root_node
    chunking = ctx.source.chunking
    max_bytes = int(chunking.max_tokens * CODE_CHARS_PER_TOKEN)
    min_bytes = int(chunking.min_tokens * CODE_CHARS_PER_TOKEN)

    ranges = _merge(_split(root, max_bytes), max_bytes, min_bytes)
    ranges = [piece for r in ranges for piece in _cut_oversized(r, src, max_bytes, chunking.overlap_tokens)]

    newlines = [i for i, b in enumerate(src) if b == 0x0A]
    chunks = []
    for r in ranges:
        start = src.rfind(b"\n", 0, r.start) + 1  # from the start of the line, for indentation
        body = src[start : r.end].decode("utf-8", errors="replace")
        body = textwrap.dedent(body).strip("\n")
        if not body.strip() or body.strip() in {"}", "};", ")", "});", "]"}:
            continue
        first = bisect.bisect_right(newlines, r.start - 1) + 1
        last = bisect.bisect_right(newlines, max(r.start, r.end - 1)) + 1
        scope = _scope(r.nodes[0], src) if r.nodes else []
        symbols = [] if r.split else _symbols(r.nodes, src)
        body = mask_secrets(body)
        chunks.append(_code_chunk(ctx, body, first, last, LANGUAGE_NAMES[language], scope, symbols, "code"))
    return chunks


def _is_comment(node) -> bool:
    return any(t in node.type for t in _COMMENT_TYPES)


def _split(node, max_bytes: int) -> list[_Range]:
    """Cut a node into ranges of whole children that each fit max_bytes."""
    if node.end_byte - node.start_byte <= max_bytes or not node.children:
        return [_Range(node.start_byte, node.end_byte, [node])]
    out: list[_Range] = []
    current: _Range | None = None
    for child in node.children:
        if child.end_byte - child.start_byte > max_bytes:
            comments = _pop_trailing_comments(current)
            if current and current.nodes:
                out.append(current)
            current = None
            parts = _split(child, max_bytes)
            if comments and parts:  # keep a doc comment with the declaration it documents
                parts[0].start = comments[0].start_byte
            out.extend(parts)
            continue
        if current and child.end_byte - current.start <= max_bytes:
            current.end = child.end_byte
            current.nodes.append(child)
            continue
        comments = _pop_trailing_comments(current)
        if current and current.nodes:
            out.append(current)
        start = comments[0].start_byte if comments else child.start_byte
        current = _Range(start, child.end_byte, comments + [child])
    if current and current.nodes:
        out.append(current)
    return out


def _pop_trailing_comments(current: _Range | None) -> list:
    if not current:
        return []
    comments = []
    while len(current.nodes) > 1 and _is_comment(current.nodes[-1]):
        comments.insert(0, current.nodes.pop())
    if comments:
        current.end = current.nodes[-1].end_byte
    return comments


def _merge(ranges: list[_Range], max_bytes: int, min_bytes: int) -> list[_Range]:
    """Join neighbouring ranges while they fit; tiny ones (a closing brace, a class header) always join."""
    merged: list[_Range] = []
    for r in ranges:
        if merged:
            last = merged[-1]
            combined = r.end - last.start
            tiny = min(last.end - last.start, r.end - r.start) < min_bytes
            if combined <= max_bytes or (tiny and combined <= max_bytes * 1.25):
                last.end = r.end
                last.nodes.extend(r.nodes)
                continue
        merged.append(r)
    return merged


def _cut_oversized(r: _Range, src: bytes, max_bytes: int, overlap_tokens: int) -> list[_Range]:
    if r.end - r.start <= max_bytes * 1.25:
        return [r]
    lines = src[r.start : r.end].decode("utf-8", errors="replace").split("\n")
    max_tokens = int(max_bytes / CODE_CHARS_PER_TOKEN)
    pieces = []
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line.encode("utf-8")) + 1)
    for w in split_lines(lines, max_tokens, overlap_tokens, CODE_CHARS_PER_TOKEN):
        start = r.start + offsets[w.first_line - 1]
        end = min(r.end, r.start + offsets[w.last_line] - 1)
        pieces.append(_Range(start, end, r.nodes[:1], split=True))
    return pieces


def _scope(node, src: bytes) -> list[str]:
    """Labels of the declarations that enclose a node, outermost first."""
    labels = []
    parent = node.parent
    while parent is not None:
        label = _container_label(parent, src)
        if label:
            labels.append(label)
        parent = parent.parent
    return list(reversed(labels))


def _container_label(node, src: bytes) -> str:
    if node.type in _CONTAINERS:
        body = node.child_by_field_name("body")
        end = body.start_byte if body is not None else node.end_byte
        signature = squash_spaces(src[node.start_byte : end].decode("utf-8", errors="replace").replace("\n", " "))
        return signature[:120]
    if node.type == "call_expression":
        return _test_call_label(node, src)
    return ""


def _test_call_label(node, src: bytes) -> str:
    callee = node.child_by_field_name("function")
    if callee is None:
        return ""
    name = src[callee.start_byte : callee.end_byte].decode("utf-8", errors="replace")
    if name not in _TEST_CALLS:
        return ""
    args = node.child_by_field_name("arguments")
    title = ""
    if args is not None:
        for arg in args.named_children:
            if arg.type in ("string", "template_string"):
                title = src[arg.start_byte : arg.end_byte].decode("utf-8", errors="replace")
                break
    return f"{name}({title[:100]})" if title else name


def _symbols(nodes: list, src: bytes) -> list[str]:
    """Names of the declarations and tests that start inside a range."""
    found: list[str] = []

    def visit(node, depth: int) -> None:
        if depth > 6 or len(found) >= 16:
            return
        if node.type in _CONTAINERS:
            name = node.child_by_field_name("name")
            callable_ = any(k in node.type for k in ("method", "function", "constructor"))
            if name is not None:
                label = src[name.start_byte : name.end_byte].decode("utf-8", errors="replace")
                found.append(f"{label}()" if callable_ else label)
            if callable_:
                return  # list a class's methods, but not what a method contains
        elif node.type == "call_expression":
            label = _test_call_label(node, src)
            if label and not label.startswith(("test.before", "test.after", "before", "after")):
                found.append(label)
                if not label.startswith(("describe", "test.describe", "context")):
                    return
        for child in node.named_children:
            visit(child, depth + 1)

    for node in nodes:
        visit(node, 0)
    return list(dict.fromkeys(found))


def _label(ctx: LoadContext) -> str:
    return ctx.source.label


def _code_chunk(
    ctx: LoadContext, body: str, first: int, last: int, language: str, scope: list[str], symbols: list[str], kind: str
) -> Chunk:
    header = [f"{_label(ctx)} · {ctx.rel_path} · lines {first}-{last}"]
    context = " > ".join(scope)
    header.append(f"{language}" + (f" · {context}" if context else ""))
    if symbols:
        header.append("Defines: " + ", ".join(symbols))
    url = ctx.repo.file_url(ctx.rel_path, first, last) if ctx.repo else ""
    return ctx.chunk(
        "\n".join(header) + "\n\n" + body,
        title=ctx.path.name,
        location=f"L{first}-L{last}",
        url=url,
        source_type="code",
        kind=kind,
        language=language,
        path=ctx.rel_path,
        scope=context,
        symbols=symbols,
    )
