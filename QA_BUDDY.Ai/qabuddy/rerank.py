"""Optional reranking of the fused candidates (RERANKER=cohere).

Off by default: hybrid retrieval with RRF is the self-hosted baseline. Cohere
Rerank re-scores the top candidates against the question and usually lifts
precision further, at the cost of one API call per question.
"""

from __future__ import annotations

import httpx

from .settings import Settings


class Reranker:
    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    @classmethod
    def from_settings(cls, settings: Settings) -> "Reranker | None":
        if settings.reranker == "cohere" and settings.cohere_api_key:
            return cls(settings.cohere_api_key, settings.rerank_model)
        return None

    def rerank(self, query: str, hits: list) -> list:
        if not hits:
            return hits
        try:
            response = httpx.post(
                "https://api.cohere.com/v2/rerank",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "query": query, "documents": [h.payload.get("text", "")[:4000] for h in hits]},
                timeout=30,
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return hits  # reranking is an improvement, never a reason to fail a question
        order = []
        for result in response.json().get("results", []):
            hit = hits[result["index"]]
            hit.rerank_score = result["relevance_score"]
            hit.ranks["rerank"] = len(order) + 1
            order.append(hit)
        return order or hits
