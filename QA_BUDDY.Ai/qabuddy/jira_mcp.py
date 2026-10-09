"""Fetch Jira tickets over MCP with a JQL query and snapshot them for ingestion.

Works with any MCP server that exposes a JQL search tool. The defaults match
mcp-atlassian (https://github.com/sooperset/mcp-atlassian): tool `jira_search`
with `jql`, `fields`, `limit` and `start_at`. Argument names are read from the
tool's input schema, so servers that call them `maxResults`/`startAt` work too.

Connect over Streamable HTTP (JIRA_MCP_URL) or by launching the server as a
subprocess over stdio (JIRA_MCP_COMMAND + JIRA_MCP_ARGS). Each ticket is saved
as storage/jira_mcp/<KEY>.json; ingestion picks them up as the "jira" source.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
from dataclasses import dataclass

from .loaders.jira import issues_from_json
from .settings import Settings

FIELDS = (
    "summary,description,status,issuetype,priority,labels,components,assignee,reporter,created,updated,"
    "resolution,fixVersions,comment,parent,issuelinks,project,environment"
)
_ENV_PREFIXES = ("JIRA_", "CONFLUENCE_", "ATLASSIAN_", "READ_ONLY_MODE", "MCP_", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")


@dataclass
class SyncResult:
    tool: str
    fetched: int
    written: int
    removed: int


def sync_jira(settings: Settings, jql: str, limit: int = 0, prune: bool = False) -> SyncResult:
    if not settings.jira_mcp_configured:
        raise RuntimeError("No Jira MCP server configured. Set JIRA_MCP_URL, or JIRA_MCP_COMMAND and JIRA_MCP_ARGS, in .env")
    issues, tool = asyncio.run(fetch_issues(settings, jql, limit))
    out = settings.jira_snapshot_dir
    out.mkdir(parents=True, exist_ok=True)
    written, keys = 0, set()
    for issue in issues:
        key = str(issue.get("key") or "")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*-\d+", key):
            continue
        keys.add(key)
        path = out / f"{key}.json"
        data = json.dumps(issue, indent=1, ensure_ascii=False, sort_keys=True)
        if not path.exists() or path.read_text(encoding="utf-8") != data:
            path.write_text(data, encoding="utf-8")
            written += 1
    removed = 0
    if prune:
        for path in out.glob("*.json"):
            if path.stem not in keys:
                path.unlink()
                removed += 1
    return SyncResult(tool, len(keys), written, removed)


async def fetch_issues(settings: Settings, jql: str, limit: int = 0) -> tuple[list[dict], str]:
    from mcp import Client

    async with Client(_server(settings), read_timeout_seconds=180) as client:
        tools = (await client.list_tools()).tools
        tool = _pick_tool(tools, settings.jira_mcp_search_tool)
        params = {name.lower().replace("_", ""): name for name in ((tool.input_schema or {}).get("properties") or {})}
        page_size = settings.jira_page_size
        issues: dict[str, dict] = {}
        start, token = 0, None
        for _ in range(10_000):  # hard stop for servers that never end a pagination
            args = {params.get("jql", "jql"): jql}
            _set(args, params, ("limit", "maxresults", "pagesize"), page_size)
            _set(args, params, ("startat", "start", "offset"), start)
            _set(args, params, ("fields",), FIELDS)
            if token:
                _set(args, params, ("nextpagetoken", "pagetoken", "cursor"), token)
            result = await client.call_tool(tool.name, args)
            data = _result_data(result)
            batch = issues_from_json(data)
            new = [i for i in batch if i.get("key") and i["key"] not in issues]
            for issue in new:
                issues[issue["key"]] = issue
            if limit and len(issues) >= limit:
                break
            total = data.get("total") if isinstance(data, dict) else None
            token = (data.get("next_page_token") or data.get("nextPageToken")) if isinstance(data, dict) else None
            if not new or (total is not None and len(issues) >= int(total)) or (len(batch) < page_size and not token):
                break
            start += len(batch)
        found = list(issues.values())
        return (found[:limit] if limit else found), tool.name


def _server(settings: Settings):
    if settings.jira_mcp_url:
        if settings.jira_mcp_token:
            from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

            http = create_mcp_http_client(headers={"Authorization": f"Bearer {settings.jira_mcp_token}"})
            return streamable_http_client(settings.jira_mcp_url, http_client=http)
        return settings.jira_mcp_url
    from mcp import StdioServerParameters
    from mcp.client.stdio import get_default_environment

    env = get_default_environment()
    env.update({k: v for k, v in os.environ.items() if k.startswith(_ENV_PREFIXES)})
    return StdioServerParameters(command=settings.jira_mcp_command, args=split_args(settings.jira_mcp_args), env=env)


def split_args(value: str) -> list[str]:
    """Shell-style split that keeps Windows backslashes and drops surrounding quotes."""
    parts = shlex.split(value, posix=False) if value else []
    return [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts]


def _pick_tool(tools, wanted: str):
    by_name = {t.name: t for t in tools}
    if wanted in by_name:
        return by_name[wanted]
    for tool in tools:  # any search tool that takes JQL
        props = (tool.input_schema or {}).get("properties") or {}
        if "jql" in {p.lower() for p in props}:
            return tool
    names = ", ".join(sorted(by_name)) or "none"
    raise RuntimeError(f"The MCP server has no JQL search tool '{wanted}'. Tools offered: {names}")


def _set(args: dict, params: dict, candidates: tuple[str, ...], value) -> None:
    for candidate in candidates:
        if candidate in params:
            args[params[candidate]] = value
            return


def _result_data(result):
    if getattr(result, "is_error", False):
        text = " ".join(getattr(c, "text", "") for c in result.content or [])
        raise RuntimeError(f"Jira MCP search failed: {text[:500]}")
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict) and ("issues" in structured or "key" in structured):
        return structured
    if isinstance(structured, dict) and isinstance(structured.get("result"), (dict, list, str)):
        structured = structured["result"]
        if not isinstance(structured, str):
            return structured
    text = "".join(getattr(c, "text", "") for c in result.content or [])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise RuntimeError(f"Jira MCP search did not return JSON: {text[:300]}") from None
