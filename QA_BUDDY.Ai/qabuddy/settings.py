"""Runtime settings, read from environment variables and an optional .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent.parent

load_dotenv(APP_DIR / ".env")


def _str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _int(name: str, default: int) -> int:
    value = _str(name)
    return int(value) if value else default


def _float(name: str, default: float) -> float:
    value = _str(name)
    return float(value) if value else default


def _bool(name: str, default: bool) -> bool:
    value = _str(name).lower()
    return value in ("1", "true", "yes", "on") if value else default


def _path(name: str, default: str) -> Path:
    path = Path(_str(name, default))
    return path if path.is_absolute() else (APP_DIR / path).resolve()


def _optional_path(name: str) -> Path | None:
    return _path(name, "") if _str(name) else None


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    storage_dir: Path
    sources_file: Path
    glossary_file: Path

    qdrant_url: str
    qdrant_api_key: str
    collection: str
    snapshot_dir: Path | None  # serve a read-only index snapshot instead of Qdrant (serverless)

    ollama_url: str
    embed_model: str
    embed_batch: int
    embed_num_ctx: int
    query_instruction: str
    embed_api_url: str  # OpenAI-compatible embeddings API instead of Ollama, e.g. Vercel AI Gateway
    embed_api_key: str
    embed_api_model: str

    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_reasoning_effort: str
    llm_max_tokens: int
    llm_temperature: float

    reranker: str
    cohere_api_key: str
    rerank_model: str

    top_k: int
    candidates: int
    context_tokens: int

    jira_url: str
    jira_jql: str
    jira_mcp_url: str
    jira_mcp_token: str
    jira_mcp_command: str
    jira_mcp_args: str
    jira_mcp_search_tool: str
    jira_page_size: int

    auto_ingest_minutes: int  # the web app re-indexes every N minutes (60 = hourly); 0, the default, is off
    auto_git_pull: bool  # pull new commits into the source repositories before each automatic run

    auth_user: str
    auth_password: str
    host: str
    port: int

    @property
    def llm_configured(self) -> bool:
        # Local OpenAI-compatible servers (Ollama, vLLM) need no key.
        local = any(h in self.llm_base_url for h in ("localhost", "127.0.0.1", "ollama:", "vllm:"))
        return bool(self.llm_base_url and self.llm_model and (self.llm_api_key or local))

    @property
    def jira_mcp_configured(self) -> bool:
        return bool(self.jira_mcp_url or self.jira_mcp_command)

    @property
    def jira_snapshot_dir(self) -> Path:
        return self.storage_dir / "jira_mcp"


@lru_cache
def get_settings() -> Settings:
    return Settings(
        data_dir=_path("QABUDDY_DATA_DIR", "../data"),
        storage_dir=_path("QABUDDY_STORAGE_DIR", "storage"),
        sources_file=_path("QABUDDY_SOURCES_FILE", "config/sources.yaml"),
        glossary_file=_path("QABUDDY_GLOSSARY_FILE", "config/glossary.yaml"),
        qdrant_url=_str("QDRANT_URL", "http://127.0.0.1:6333"),
        qdrant_api_key=_str("QDRANT_API_KEY"),
        collection=_str("QDRANT_COLLECTION", "qabuddy"),
        snapshot_dir=_optional_path("QABUDDY_SNAPSHOT_DIR"),
        ollama_url=_str("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
        embed_model=_str("EMBED_MODEL", "qwen3-embedding:0.6b"),
        embed_batch=_int("EMBED_BATCH", 16),
        embed_num_ctx=_int("EMBED_NUM_CTX", 2048),
        query_instruction=_str(
            "EMBED_QUERY_INSTRUCTION",
            "Given a question from a QA engineer, retrieve the code, test cases, requirements, "
            "tickets, notes or build logs that answer it",
        ),
        embed_api_url=_str("EMBED_API_URL").rstrip("/"),
        embed_api_key=_str("EMBED_API_KEY") or _str("AI_GATEWAY_API_KEY"),
        embed_api_model=_str("EMBED_API_MODEL", "alibaba/qwen3-embedding-0.6b"),
        llm_base_url=_str("LLM_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/"),
        llm_api_key=_str("LLM_API_KEY") or _str("GROQ_API_KEY"),
        llm_model=_str("LLM_MODEL", "openai/gpt-oss-120b"),
        llm_reasoning_effort=_str("LLM_REASONING_EFFORT", "low"),
        llm_max_tokens=_int("LLM_MAX_TOKENS", 1500),
        llm_temperature=_float("LLM_TEMPERATURE", 0.1),
        reranker=_str("RERANKER", "none").lower(),
        cohere_api_key=_str("COHERE_API_KEY") or _str("CO_HERE_API_KEY"),
        rerank_model=_str("RERANK_MODEL", "rerank-english-v3.0"),
        top_k=_int("TOP_K", 8),
        candidates=_int("RETRIEVAL_CANDIDATES", 30),
        context_tokens=_int("CONTEXT_TOKENS", 4000),
        jira_url=_str("JIRA_URL").rstrip("/"),
        jira_jql=_str("JIRA_JQL"),
        jira_mcp_url=_str("JIRA_MCP_URL"),
        jira_mcp_token=_str("JIRA_MCP_TOKEN"),
        jira_mcp_command=_str("JIRA_MCP_COMMAND"),
        jira_mcp_args=_str("JIRA_MCP_ARGS"),
        jira_mcp_search_tool=_str("JIRA_MCP_SEARCH_TOOL", "jira_search"),
        jira_page_size=_int("JIRA_PAGE_SIZE", 50),
        auto_ingest_minutes=_int("QABUDDY_AUTO_INGEST_MINUTES", 0),
        auto_git_pull=_bool("QABUDDY_AUTO_GIT_PULL", True),
        auth_user=_str("QABUDDY_USERNAME"),
        auth_password=_str("QABUDDY_PASSWORD"),
        host=_str("QABUDDY_HOST", "127.0.0.1"),
        port=_int("QABUDDY_PORT", 8000),
    )
