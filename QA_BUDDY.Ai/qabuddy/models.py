"""Shared data types."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

# Fixed namespace so the same document position always maps to the same point id.
POINT_NAMESPACE = uuid.UUID("6f1c2a52-8f3e-4b1e-9a57-2b7d3c9e0a11")


@dataclass
class Hit:
    """One search result from either retriever."""

    id: str
    score: float
    payload: dict


@dataclass
class Chunk:
    """One retrievable piece of a document, with everything needed to cite it."""

    text: str  # what is embedded, indexed and shown to the LLM
    source: str  # source key from sources.yaml, e.g. "selenium"
    source_type: str  # loader kind, e.g. "code", "test_case"
    doc_id: str  # the file (relative to the data folder) the chunk came from
    title: str  # short label for citations, e.g. "LoginPage.java" or "INVALID-016"
    location: str = ""  # where inside the document: "L12-L40", "row 42", "p. 3"
    url: str = ""  # link to the original (GitHub, Jira), when known
    meta: dict = field(default_factory=dict)  # source-specific extras

    def point_id(self, index: int) -> str:
        return str(uuid.uuid5(POINT_NAMESPACE, f"{self.doc_id}#{index}"))

    def payload(self) -> dict:
        return {
            "text": self.text,
            "source": self.source,
            "source_type": self.source_type,
            "doc_id": self.doc_id,
            "title": self.title,
            "location": self.location,
            "url": self.url,
            "meta": self.meta,
        }
