"""Question -> hybrid retrieval -> grounded, cited answer (streamed).

Token budget: only the top chunks that fit CONTEXT_TOKENS (default 4,000) are
sent, history is limited to the last three exchanges with old citations
stripped, and the model answers in one call. A typical question costs about
4-5k prompt tokens.
"""

from __future__ import annotations

import re
import time
from typing import Iterator

from . import prompts
from .llm import LLM, LLMError
from .retrieval import Retrieved, Retriever
from .settings import Settings
from .sources import load_catalog
from .text import estimate_tokens

_CITATION = re.compile(r"\[(\d{1,2}(?:\s*[,;]\s*\d{1,2})*)\]")
_FOLLOW_UP = re.compile(r"\b(it|its|this|that|these|those|them|they|same|above|previous|again|more|also)\b", re.I)
MAX_HISTORY_TURNS = 6

# gpt-oss models sometimes fall back to their browsing citation style, 【1†L1-L9】.
_TOOL_CITATION = re.compile(r"[【\[](\d{1,2})†[^】\]\n]{0,40}[】\]]|【(\d{1,2})】")
_OPEN_CITATION = re.compile(r"[【\[]\d{0,2}(?:†[^】\]\n]*)?$")


class CitationStyle:
    """Rewrites 【n†...】 citations to [n] while the answer streams, even when one is split across pieces."""

    def __init__(self) -> None:
        self.pending = ""

    def feed(self, text: str) -> str:
        self.pending += text
        open_tail = _OPEN_CITATION.search(self.pending)
        if open_tail and len(self.pending) - open_tail.start() < 60:
            ready, self.pending = self.pending[: open_tail.start()], self.pending[open_tail.start() :]
        else:
            ready, self.pending = self.pending, ""
        return _TOOL_CITATION.sub(_plain, ready)

    def flush(self) -> str:
        rest, self.pending = self.pending, ""
        return _TOOL_CITATION.sub(_plain, rest)


def _plain(match: re.Match) -> str:
    return f"[{match.group(1) or match.group(2)}]"


def answer_stream(
    settings: Settings,
    question: str,
    history: list[dict] | None = None,
    mode: str = "ask",
    sources: list[str] | None = None,
    top_k: int | None = None,
    retriever: Retriever | None = None,
    llm: LLM | None = None,
) -> Iterator[tuple[str, object]]:
    """Yields ("sources", ...), then ("delta", text) pieces, then ("done", ...) or ("error", ...)."""
    started = time.time()
    history = history or []
    task = prompts.mode(mode)
    if sources is None:
        sources = list(task.sources) or None
    retriever = retriever or Retriever(settings)
    llm = llm or LLM(settings)
    labels = {s.key: s.label for s in load_catalog(settings).sources}

    query = retrieval_query(question, history)
    try:
        hits = retriever.search(query, sources=sources, top_k=top_k)
    except Exception as error:  # Qdrant or Ollama down
        yield "error", {"message": f"Retrieval failed: {error}"}
        return
    used = pack_context(hits, settings.context_tokens)
    items = [source_item(n, hit, labels) for n, hit in enumerate(used, 1)]
    retrieved_at = time.time()
    yield "sources", {"sources": items, "query": query, "mode": task.key, "filters": sources}

    done = {
        "sources": items,
        "cited": [],
        "usage": {},
        "model": llm.model if llm.configured else None,
        "provider": llm.provider if llm.configured else None,
        "timings": {"retrieval_ms": int((retrieved_at - started) * 1000)},
    }
    if not used:
        scope = f" in {', '.join(labels.get(s, s) for s in sources)}" if sources else ""
        yield "delta", f"I couldn't find anything relevant{scope}. Try other words, or widen the source filter."
        yield "done", done
        return
    if not llm.configured:
        yield "delta", retrieval_only_answer(items)
        done["cited"] = [item["n"] for item in items]
        done["notice"] = "No LLM is configured, so these are the best matching sources. Set GROQ_API_KEY in .env for written answers."
        yield "done", done
        return

    parts: list[str] = []
    usage: dict = {}
    style = CitationStyle()
    try:
        for kind, value in llm.stream(build_messages(question, history, task, used, labels)):
            if kind == "delta":
                text = style.feed(value)
                if text:
                    parts.append(text)
                    yield "delta", text
            elif kind == "usage":
                usage = value or {}
    except LLMError as error:
        yield "error", {"message": str(error)}
        return
    rest = style.flush()
    if rest:
        parts.append(rest)
        yield "delta", rest
    answer = "".join(parts)
    done.update(
        cited=parse_citations(answer, len(used)),
        usage={k: usage.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens") if k in usage},
    )
    done["timings"]["total_ms"] = int((time.time() - started) * 1000)
    done["timings"]["generation_ms"] = int((time.time() - retrieved_at) * 1000)
    yield "done", done


def retrieval_query(question: str, history: list[dict]) -> str:
    """Follow-ups like "now write them in Playwright" are searched together with the previous question."""
    previous = next((t["content"] for t in reversed(history) if t.get("role") == "user"), "")
    if previous and (len(question.split()) <= 12 or _FOLLOW_UP.search(question)):
        return f"{previous}\n{question}"
    return question


def pack_context(hits: list[Retrieved], budget_tokens: int) -> list[Retrieved]:
    used, total = [], 0
    for hit in hits:
        tokens = estimate_tokens(hit.payload.get("text", ""))
        if total + tokens > budget_tokens:
            if not used:  # always send the best match, trimmed
                hit.payload = {**hit.payload, "text": hit.payload.get("text", "")[: budget_tokens * 4]}
                used.append(hit)
            continue
        used.append(hit)
        total += tokens
    return used


def source_item(n: int, hit: Retrieved, labels: dict[str, str]) -> dict:
    p = hit.payload
    return {
        "n": n,
        "id": hit.id,
        "source": p.get("source"),
        "label": labels.get(p.get("source"), p.get("source")),
        "source_type": p.get("source_type"),
        "title": p.get("title"),
        "location": p.get("location", ""),
        "url": p.get("url", ""),
        "doc_id": p.get("doc_id"),
        "score": round(hit.score, 4),
        "ranks": hit.ranks,
        "text": p.get("text", ""),
    }


def build_messages(question: str, history: list[dict], task: prompts.Mode, used: list[Retrieved], labels: dict[str, str]) -> list[dict]:
    system = prompts.SYSTEM_PROMPT + f"\n\nTask: {task.label}. {task.instructions}"
    messages = [{"role": "system", "content": system}]
    for turn in history[-MAX_HISTORY_TURNS:]:
        content = turn.get("content", "")
        if turn.get("role") == "assistant":
            content = _CITATION.sub("", content)[:2000]  # old citation numbers point at old sources
        if turn.get("role") in ("user", "assistant") and content.strip():
            messages.append({"role": turn["role"], "content": content})
    blocks = []
    for n, hit in enumerate(used, 1):
        p = hit.payload
        where = " · ".join(x for x in (labels.get(p.get("source"), p.get("source")), p.get("title"), p.get("location")) if x)
        blocks.append(f"[{n}] {where}\n{p.get('text', '')}")
    messages.append({"role": "user", "content": "Sources:\n\n" + "\n\n".join(blocks) + f"\n\nQuestion: {question}"})
    return messages


def parse_citations(answer: str, count: int) -> list[int]:
    cited: set[int] = set()
    for match in _CITATION.finditer(answer):
        for number in re.split(r"\s*[,;]\s*", match.group(1)):
            if number.isdigit() and 1 <= int(number) <= count:
                cited.add(int(number))
    return sorted(cited)


def retrieval_only_answer(items: list[dict]) -> str:
    lines = ["Best matching sources:", ""]
    for item in items:
        first = next((l for l in item["text"].split("\n")[1:] if l.strip()), "")[:160]
        where = " · ".join(x for x in (item["label"], item["location"]) if x)
        lines.append(f"{item['n']}. **{item['title']}** · {where} [{item['n']}]  \n   {first}")
    return "\n".join(lines)
