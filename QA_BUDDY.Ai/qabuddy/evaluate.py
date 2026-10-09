"""Retrieval evaluation: recall@k and MRR for semantic, keyword and hybrid search."""

from __future__ import annotations

from pathlib import Path

import yaml

from .retrieval import Retriever
from .settings import APP_DIR, Settings

MODES = ("semantic", "keyword", "hybrid")


def _matches(hit, expect: list[str]) -> bool:
    haystack = f"{hit.payload.get('title', '')} {hit.payload.get('doc_id', '')}".lower()
    return any(e.lower() in haystack for e in expect)


def run_eval(settings: Settings, file: str, k: int = 5, verbose: bool = False) -> int:
    path = Path(file)
    if not path.is_absolute():
        path = APP_DIR / path
    questions = yaml.safe_load(path.read_text(encoding="utf-8"))["questions"]
    retriever = Retriever(settings)
    totals = {m: {"hits": 0, "mrr": 0.0} for m in MODES}
    rows = []
    for item in questions:
        ranks = {}
        for mode in MODES:
            hits = retriever.search(item["q"], top_k=k, mode=mode)
            rank = next((i for i, h in enumerate(hits, 1) if _matches(h, item["expect"])), None)
            ranks[mode] = rank
            if rank:
                totals[mode]["hits"] += 1
                totals[mode]["mrr"] += 1 / rank
        rows.append((item["q"], ranks))

    if verbose:
        print(f"{'question':<70} " + " ".join(f"{m:>9}" for m in MODES))
        for question, ranks in rows:
            cells = " ".join(f"{('#' + str(ranks[m])) if ranks[m] else '-':>9}" for m in MODES)
            print(f"{question[:69]:<70} {cells}")
        print()
    n = len(questions)
    print(f"{n} questions, top {k}")
    print(f"{'mode':<10}{'recall@' + str(k):>11}{'MRR':>8}")
    for mode in MODES:
        print(f"{mode:<10}{totals[mode]['hits'] / n:>11.0%}{totals[mode]['mrr'] / n:>8.2f}")
    return 0
