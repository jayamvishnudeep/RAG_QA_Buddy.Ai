"""Command line: python -m qabuddy <command>.

  ingest            index the data folders (incremental; --rebuild starts over)
  sync-jira         fetch tickets over MCP with a JQL query, then ingest them
  search            show what retrieval finds for a query, without the LLM
  ask               answer a question in the terminal
  stats             chunks per source
  serve             start the web app
  eval              measure retrieval quality on eval/questions.yaml
  export-snapshot   write the index to a folder for serverless hosting (Vercel)
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def cmd_ingest(args) -> int:
    from .ingest import run_ingest
    from .settings import get_settings

    report = run_ingest(get_settings(), only=args.source or None, rebuild=args.rebuild)
    print(f"\nDone in {report.seconds}s. The index holds {report.total_chunks} chunks.")
    print(f"{'source':<14}{'files':>7}{'indexed':>9}{'same':>6}{'skipped':>9}{'errors':>8}{'chunks':>8}")
    for key, rep in report.sources.items():
        print(f"{key:<14}{rep.files:>7}{rep.indexed:>9}{rep.unchanged:>6}{rep.skipped:>9}{rep.errors:>8}{rep.chunks:>8}")
        for note in rep.notes:
            print(f"{'':<14}note: {note}")
    for error in report.errors:
        print(f"error: {error}")
    return 1 if report.errors else 0


def cmd_sync_jira(args) -> int:
    from .ingest import run_ingest
    from .jira_mcp import sync_jira
    from .settings import get_settings

    settings = get_settings()
    jql = args.jql or settings.jira_jql
    if not jql:
        print("No JQL given. Pass --jql or set JIRA_JQL in .env", file=sys.stderr)
        return 2
    result = sync_jira(settings, jql, limit=args.limit, prune=args.prune)
    print(f"Fetched {result.fetched} tickets via MCP tool '{result.tool}' -> {result.written} written, {result.removed} removed")
    if not args.no_ingest:
        run_ingest(settings, only=["jira"])
    return 0


def cmd_search(args) -> int:
    from .retrieval import Retriever
    from .settings import get_settings

    retriever = Retriever(get_settings())
    hits = retriever.search(args.query, sources=args.source or None, top_k=args.k, mode=args.mode)
    for n, hit in enumerate(hits, 1):
        p = hit.payload
        ranks = ", ".join(f"{k} #{v}" for k, v in hit.ranks.items())
        print(f"[{n}] {p['source']:<12} {p['title']} · {p.get('location', '')}  ({ranks}; score {hit.score:.4f})")
        if args.show:
            print(textwrap.indent(p["text"][: args.show], "      ") + "\n")
    return 0


def cmd_ask(args) -> int:
    from .answer import answer_stream
    from .settings import get_settings

    sources_printed = False
    for event, data in answer_stream(get_settings(), args.question, mode=args.mode, sources=args.source or None):
        if event == "sources" and not sources_printed:
            sources_printed = True
        elif event == "delta":
            print(data, end="", flush=True)
        elif event == "done":
            print("\n\nSources:")
            for s in data["sources"]:
                mark = "*" if s["n"] in data["cited"] else " "
                print(f" {mark}[{s['n']}] {s['label']} · {s['title']} · {s['location']}  {s.get('url', '')}")
            usage = data.get("usage") or {}
            if usage:
                print(f"\nTokens: {usage.get('prompt_tokens', '?')} in, {usage.get('completion_tokens', '?')} out")
        elif event == "error":
            print(f"\nError: {data['message']}", file=sys.stderr)
            return 1
    return 0


def cmd_stats(args) -> int:
    from .settings import get_settings
    from .store import Store

    store = Store(get_settings())
    print(json.dumps({"total": store.count(), "by_source": store.counts_by("source")}, indent=2))
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    from .settings import get_settings

    settings = get_settings()
    uvicorn.run("qabuddy.api:app", host=args.host or settings.host, port=args.port or settings.port, reload=args.reload)
    return 0


def cmd_eval(args) -> int:
    from .evaluate import run_eval
    from .settings import get_settings

    return run_eval(get_settings(), args.file, k=args.k, verbose=args.verbose)


def cmd_export_snapshot(args) -> int:
    from pathlib import Path

    from .prompts import MODES
    from .settings import get_settings
    from .snapshot import export_snapshot

    seed = [question for mode in MODES.values() for question in mode.examples]
    meta = export_snapshot(get_settings(), Path(args.out), seed_questions=seed)
    print(f"Exported {meta['count']} chunks ({meta['dim']}-dim vectors) and {meta['seeded_questions']} example questions to {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    parser = argparse.ArgumentParser(prog="qabuddy", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest", help="index the data folders")
    p.add_argument("--source", action="append", help="only this source key (repeatable)")
    p.add_argument("--rebuild", action="store_true", help="drop the collection and index everything again")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("sync-jira", help="fetch Jira tickets over MCP, then ingest them")
    p.add_argument("--jql", help="JQL query (default: JIRA_JQL from .env)")
    p.add_argument("--limit", type=int, default=0, help="stop after this many tickets (0 = all)")
    p.add_argument("--prune", action="store_true", help="remove snapshots of tickets the JQL no longer returns")
    p.add_argument("--no-ingest", action="store_true", help="only write the snapshots")
    p.set_defaults(func=cmd_sync_jira)

    p = sub.add_parser("search", help="retrieval only")
    p.add_argument("query")
    p.add_argument("--source", action="append")
    p.add_argument("--mode", choices=["hybrid", "semantic", "keyword"], default="hybrid")
    p.add_argument("-k", type=int, default=8)
    p.add_argument("--show", type=int, default=0, metavar="CHARS", help="print the first CHARS of each chunk")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("ask", help="answer a question")
    p.add_argument("question")
    p.add_argument("--mode", default="ask")
    p.add_argument("--source", action="append")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("stats", help="chunks per source")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("serve", help="start the web app")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--reload", action="store_true")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("eval", help="measure retrieval quality")
    p.add_argument("--file", default="eval/questions.yaml")
    p.add_argument("-k", type=int, default=5)
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("export-snapshot", help="write the index to a folder for serverless hosting")
    p.add_argument("--out", default="storage/index_snapshot", help="target folder")
    p.set_defaults(func=cmd_export_snapshot)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
