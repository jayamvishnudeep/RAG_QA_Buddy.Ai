"""Chat completions from any OpenAI-compatible endpoint, streamed.

Groq is the default (fast, cheap, open-weight models such as gpt-oss-120b).
Pointing LLM_BASE_URL at Ollama or vLLM on the droplet makes the whole stack
self-hosted without code changes.
"""

from __future__ import annotations

import json
import time
from typing import Iterator

import httpx

from .settings import Settings

MAX_RATE_LIMIT_WAIT = 30  # seconds; longer waits are reported to the user instead


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, settings: Settings):
        self.settings = settings
        headers = {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}
        self._client = httpx.Client(base_url=settings.llm_base_url, headers=headers, timeout=httpx.Timeout(180, connect=10))

    @property
    def configured(self) -> bool:
        return self.settings.llm_configured

    @property
    def model(self) -> str:
        return self.settings.llm_model

    @property
    def provider(self) -> str:
        url = self.settings.llm_base_url
        if "groq.com" in url:
            return "Groq"
        if "11434" in url or "ollama" in url:
            return "Ollama"
        if "openai.com" in url:
            return "OpenAI"
        return "OpenAI-compatible"

    def stream(self, messages: list[dict]) -> Iterator[tuple[str, object]]:
        """Yields ("delta", text) while the answer streams, then ("usage", dict)."""
        body = {
            "model": self.settings.llm_model,
            "messages": messages,
            "stream": True,
            "temperature": self.settings.llm_temperature,
            "max_tokens": self.settings.llm_max_tokens,
            "stream_options": {"include_usage": True},
        }
        if self.settings.llm_reasoning_effort and _is_reasoning_model(self.settings.llm_model):
            body["reasoning_effort"] = self.settings.llm_reasoning_effort
        waited = False
        while True:  # both errors arrive before any text, so retrying never duplicates output
            try:
                yield from self._stream(body)
                return
            except _Retry:
                if "stream_options" not in body and "reasoning_effort" not in body:
                    raise LLMError("The LLM endpoint rejected the request parameters.") from None
                for optional in ("stream_options", "reasoning_effort"):
                    body.pop(optional, None)
            except _RateLimited as limited:
                # Free tiers allow a few questions per minute; a short wait beats an error.
                if waited or limited.seconds > MAX_RATE_LIMIT_WAIT:
                    raise LLMError(_explain(429, "", self.settings)) from None
                waited = True
                time.sleep(limited.seconds)

    def _stream(self, body: dict) -> Iterator[tuple[str, object]]:
        usage: dict = {}
        thinking = _ThinkFilter()
        try:
            with self._client.stream("POST", "/chat/completions", json=body) as response:
                if response.status_code >= 400:
                    detail = response.read().decode("utf-8", errors="replace")[:500]
                    if response.status_code == 400 and any(k in detail for k in ("stream_options", "reasoning_effort")):
                        raise _Retry()
                    if response.status_code == 429:
                        raise _RateLimited(_retry_after(response.headers.get("retry-after", "")))
                    raise LLMError(_explain(response.status_code, detail, self.settings))
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    usage = chunk.get("usage") or (chunk.get("x_groq") or {}).get("usage") or usage
                    for choice in chunk.get("choices") or []:
                        text = (choice.get("delta") or {}).get("content")
                        if text:
                            visible = thinking.feed(text)
                            if visible:
                                yield "delta", visible
        except httpx.HTTPError as error:
            raise LLMError(f"Could not reach the LLM at {self.settings.llm_base_url}: {error}") from error
        rest = thinking.flush()
        if rest:
            yield "delta", rest
        yield "usage", usage


class _Retry(Exception):
    """The provider rejected an optional parameter; try again without it."""


class _RateLimited(Exception):
    def __init__(self, seconds: float):
        super().__init__(seconds)
        self.seconds = seconds


def _retry_after(header: str) -> float:
    try:
        return max(1.0, float(header))
    except ValueError:
        return 10.0


def _is_reasoning_model(model: str) -> bool:
    return any(k in model.lower() for k in ("gpt-oss", "o1", "o3", "o4", "deepseek-r1", "qwq"))


def _explain(status: int, detail: str, settings: Settings) -> str:
    if status == 401:
        return "The LLM API key was rejected (401). Check LLM_API_KEY / GROQ_API_KEY in .env."
    if status == 404 or "does not exist" in detail or "decommissioned" in detail:
        return f"Model '{settings.llm_model}' is not available at {settings.llm_base_url}. Set LLM_MODEL to a current model."
    if status == 429:
        return (
            "The LLM provider is rate limiting requests (429). Wait a minute and ask again. "
            "Free Groq keys allow only a few questions per minute; a paid tier or a local model removes the limit."
        )
    if status == 413 or "context" in detail.lower() and "length" in detail.lower():
        return "The question plus its sources is too long for the model. Lower CONTEXT_TOKENS or TOP_K."
    return f"LLM request failed ({status}): {detail}"


def _partial_suffix(text: str, tag: str) -> int:
    """Length of the longest end of `text` that is the start of `tag`."""
    for size in range(min(len(tag) - 1, len(text)), 0, -1):
        if text.endswith(tag[:size]):
            return size
    return 0


class _ThinkFilter:
    """Hides <think>...</think> blocks that some local reasoning models stream as content."""

    def __init__(self) -> None:
        self.inside = False
        self.buffer = ""

    def feed(self, text: str) -> str:
        self.buffer += text
        out = []
        while self.buffer:
            if self.inside:
                end = self.buffer.find("</think>")
                if end < 0:
                    self.buffer = self.buffer[-8:]
                    return "".join(out)
                self.buffer = self.buffer[end + 8 :].lstrip()
                self.inside = False
            else:
                start = self.buffer.find("<think>")
                if start < 0:
                    keep = _partial_suffix(self.buffer, "<think>")  # "<thi" may complete in the next piece
                    out.append(self.buffer[: len(self.buffer) - keep])
                    self.buffer = self.buffer[len(self.buffer) - keep :]
                    return "".join(out)
                out.append(self.buffer[:start])
                self.buffer = self.buffer[start + 7 :]
                self.inside = True
        return "".join(out)

    def flush(self) -> str:
        rest, self.buffer = ("" if self.inside else self.buffer), ""
        return rest
