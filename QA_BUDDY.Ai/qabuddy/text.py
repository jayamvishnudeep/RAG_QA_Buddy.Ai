"""Text normalization, secret masking, token estimates and QA terminology."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROSE_CHARS_PER_TOKEN = 4.0
CODE_CHARS_PER_TOKEN = 3.3

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
_TRAILING_SPACE = re.compile(r"[ \t]+\n")
_MANY_BLANK_LINES = re.compile(r"\n{3,}")


def estimate_tokens(text: str, chars_per_token: float = PROSE_CHARS_PER_TOKEN) -> int:
    """Rough token count. Good enough for sizing chunks and context budgets."""
    return max(1, math.ceil(len(text) / chars_per_token)) if text else 0


def normalize(text: str, keep_tabs: bool = False) -> str:
    """Canonical form for every source: NFKC, LF line endings, no invisible characters."""
    text = unicodedata.normalize("NFKC", text).translate(_ZERO_WIDTH)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not keep_tabs:
        text = text.replace("\t", "    ")
    text = _CONTROL.sub("", text)
    text = _TRAILING_SPACE.sub("\n", text)
    text = _MANY_BLANK_LINES.sub("\n\n", text)
    return text.strip()


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def squash_spaces(line: str) -> str:
    return re.sub(r"[ \t]{2,}", " ", line).strip()


# --- Secret masking -----------------------------------------------------------
# Applied before anything is embedded or stored, so credentials never reach the
# vector database or the LLM provider.

MASK = "***"
_SECRET_KEY = r"[\w.\-]*(?:password|passwd|pwd|secret|token|api[_\-]?key|apikey|access[_\-]?key|private[_\-]?key|client[_\-]?secret|auth[_\-]?key)[\w.\-]*"

_SECRET_PATTERNS = [
    # -----BEGIN PRIVATE KEY----- blocks
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"), "-----PRIVATE KEY " + MASK + "-----"),
    # Authorization: Bearer <token>
    (re.compile(r"(?i)\b((?:authorization|proxy-authorization)\s*[:=]\s*[\"']?(?:bearer|basic|token)\s+)[A-Za-z0-9._~+/=\-]{8,}"), r"\1" + MASK),
    # user:password@host in URLs
    (re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^/\s:@]+:[^/\s@]+@"), r"\1" + MASK + ":" + MASK + "@"),
    # Well-known token formats
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), MASK),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), MASK),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b"), MASK),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b"), MASK),
    (re.compile(r"\b(?:sk|gsk|pk)[-_][A-Za-z0-9_\-]{20,}\b"), MASK),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"), MASK),
    # password = "literal" in code (only quoted literals, so code like `password: process.env.X` stays readable)
    (re.compile(r"(?i)\b(" + _SECRET_KEY + r")(\s*[:=]\s*)([\"'])[^\"'\n]{3,}\3"), r"\1\2\3" + MASK + r"\3"),
]

# key=value lines in config files (.properties, .env, .yml, .ini): unquoted values too.
_CONFIG_SECRET = re.compile(r"(?im)^(\s*(?:export\s+)?" + _SECRET_KEY + r"\s*[:=]\s*)([\"']?)([^\s\"'#][^\n#]*?)\2\s*$")
# key=value anywhere in a line, for build logs (-Dpassword=..., env dumps).
_INLINE_SECRET = re.compile(r"(?i)\b(" + _SECRET_KEY + r")(\s*[=:]\s*)(?![\"'\s*])([^\s\"',;&]+)")
CONFIG_SUFFIXES = {".properties", ".env", ".ini", ".cfg", ".conf", ".yml", ".yaml", ".toml"}


def mask_secrets(text: str, config_file: bool = False, inline: bool = False) -> str:
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    if config_file:
        text = _CONFIG_SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}{m.group(2)}", text)
    if inline:
        text = _INLINE_SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", text)
    return text


def is_config_file(path: Path) -> bool:
    name = path.name.lower()
    return path.suffix.lower() in CONFIG_SUFFIXES or name.startswith(".env")


# --- QA terminology -----------------------------------------------------------


@dataclass
class Glossary:
    """Abbreviations and synonyms used to widen keyword queries."""

    abbreviations: dict[str, str] = field(default_factory=dict)
    synonyms: list[list[str]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "Glossary":
        if not path.exists():
            return cls()
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        abbreviations = {str(k).lower(): str(v).lower() for k, v in (data.get("abbreviations") or {}).items()}
        synonyms = [[str(t).lower() for t in group] for group in (data.get("synonyms") or [])]
        return cls(abbreviations, synonyms)

    def expansions(self, query: str) -> list[str]:
        """Extra terms to add to a keyword query; the query itself is not repeated."""
        lowered = query.lower()
        words = set(re.findall(r"[a-z0-9/\-]+", lowered))
        extra: list[str] = []
        for word in words:
            if word in self.abbreviations:
                extra.append(self.abbreviations[word])
        for group in self.synonyms:
            if any(_contains_term(lowered, words, term) for term in group):
                extra.extend(t for t in group if not _contains_term(lowered, words, t))
        return extra


def _contains_term(lowered: str, words: set[str], term: str) -> bool:
    if " " in term or "/" in term:
        return term in lowered
    return term in words
