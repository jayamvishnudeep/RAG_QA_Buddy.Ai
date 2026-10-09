"""Jira tickets: one ticket is one chunk.

Reads tickets from files in the Jira folder (REST/JSON exports, Jira's CSV
export, and Jira's printable/Word view saved as text, Markdown or HTML) and
from the snapshots written by `qabuddy sync-jira` over MCP. All shapes are
normalized to one ticket layout, so they are indexed the same way.
"""

from __future__ import annotations

import csv
import datetime as dt
import html
import io
import json
import re
from collections import defaultdict

from ..chunking import split_text
from ..models import Chunk
from ..text import estimate_tokens, mask_secrets, normalize
from .base import LoadContext, Unsupported, read_text

TEXT_EXPORTS = {".md", ".txt", ".html", ".htm", ".doc"}


def load(ctx: LoadContext) -> list[Chunk]:
    suffix = ctx.path.suffix.lower()
    if suffix == ".json":
        issues = issues_from_json(json.loads(read_text(ctx.path)))
    elif suffix == ".csv":
        issues = issues_from_csv(read_text(ctx.path))
    elif suffix in TEXT_EXPORTS:
        issues = issues_from_text(read_text(ctx.path))
        if not issues:
            raise Unsupported("no '[KEY-123] Summary' ticket header found")
    else:
        raise Unsupported(f"{suffix} is not a Jira export (use JSON, CSV or the printable view)")
    chunks: list[Chunk] = []
    for raw in issues:
        issue = normalize_issue(raw, ctx.settings.jira_url)
        if issue:
            chunks += ticket_chunks(ctx, issue)
    return chunks


def issues_from_json(data) -> list[dict]:
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        if isinstance(data.get("issues"), list):
            return data["issues"]
        if data.get("key"):
            return [data]
    return []


# --- Normalization ----------------------------------------------------------------


def normalize_issue(raw: dict, jira_url: str = "") -> dict | None:
    """Map a Jira REST issue, an mcp-atlassian issue or a CSV row to one flat ticket dict."""
    fields = raw.get("fields") if isinstance(raw.get("fields"), dict) else None
    get = (lambda k, *alt: _first(fields, k, *alt)) if fields else (lambda k, *alt: _first(raw, k, *alt))
    key = raw.get("key") or raw.get("issue_key") or ""
    if not key:
        return None
    comments_raw = get("comment", "comments")
    if isinstance(comments_raw, dict):
        comments_raw = comments_raw.get("comments", [])
    comments = []
    for c in comments_raw or []:
        if isinstance(c, dict):
            body = adf_to_text(c.get("body"))
            if body.strip():
                comments.append({"author": _person(c.get("author")), "created": _date(c.get("created")), "body": body})
        elif isinstance(c, str) and c.strip():
            comments.append({"author": "", "created": "", "body": c})
    links = []
    for link in get("issuelinks", "issue_links") or []:
        if not isinstance(link, dict):
            continue
        kind = link.get("type", {}) if isinstance(link.get("type"), dict) else {}
        if link.get("outwardIssue"):
            links.append(f"{kind.get('outward', 'relates to')} {link['outwardIssue'].get('key', '')}")
        elif link.get("inwardIssue"):
            links.append(f"{kind.get('inward', 'relates to')} {link['inwardIssue'].get('key', '')}")
    parent = get("parent")
    url = raw.get("url") or ""
    if not url and jira_url:
        url = f"{jira_url}/browse/{key}"
    return {
        "key": key,
        "summary": str(get("summary") or ""),
        "project": _name(get("project")),
        "type": _name(get("issuetype", "issue_type", "type")),
        "status": _name(get("status")),
        "priority": _name(get("priority")),
        "resolution": _name(get("resolution")),
        "assignee": _person(get("assignee")),
        "reporter": _person(get("reporter")),
        "labels": [str(x) for x in (get("labels") or []) if x],
        "components": [_name(x) for x in (get("components") or []) if x],
        "fix_versions": [_name(x) for x in (get("fixVersions", "fix_versions") or []) if x],
        "sprint": _name(get("sprint")),
        "created": _date(get("created")),
        "updated": _date(get("updated")),
        "parent": parent.get("key", "") if isinstance(parent, dict) else str(parent or ""),
        "links": links,
        "environment": adf_to_text(get("environment")),
        "description": adf_to_text(get("description")),
        "comments": comments,
        "url": url,
    }


def _first(d: dict, *keys):
    for k in keys:
        if d.get(k) not in (None, "", []):
            return d[k]
    return None


def _name(value) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("value") or value.get("displayName") or value.get("display_name") or "")
    if isinstance(value, list):
        return ", ".join(_name(v) for v in value if v)
    return str(value or "")


def _person(value) -> str:
    if isinstance(value, dict):
        return str(value.get("displayName") or value.get("display_name") or value.get("name") or value.get("emailAddress") or "")
    return str(value or "")


def _date(value) -> str:
    """ISO date from Jira's ISO timestamps or its '04/Apr/26 10:15 AM' display format."""
    text = str(value or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", text):
        return text[:10]
    match = re.match(r"^(\d{1,2}/[A-Za-z]{3}/\d{2,4})", text)
    if match:
        for fmt in ("%d/%b/%y", "%d/%b/%Y"):
            try:
                return dt.datetime.strptime(match.group(1), fmt).date().isoformat()
            except ValueError:
                continue
    return text[:20]


def adf_to_text(node) -> str:
    """Atlassian Document Format (Jira Cloud rich text) to plain text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_to_text(n) for n in node)
    if not isinstance(node, dict):
        return str(node)
    kind = node.get("type")
    attrs = node.get("attrs") or {}
    if kind == "text":
        return node.get("text", "")
    if kind == "hardBreak":
        return "\n"
    if kind in ("mention", "emoji", "status"):
        return attrs.get("text", "")
    if kind in ("inlineCard", "blockCard"):
        return attrs.get("url", "")
    inner = adf_to_text(node.get("content", []))
    if kind in ("paragraph", "heading"):
        return inner + "\n"
    if kind == "listItem":
        return "- " + inner.strip() + "\n"
    if kind == "codeBlock":
        return "```\n" + inner + "\n```\n"
    if kind == "tableRow":
        return " | ".join(adf_to_text(c).strip() for c in node.get("content", [])) + "\n"
    return inner


# --- Jira CSV export ----------------------------------------------------------------

_CSV_FIELDS = {
    "issue key": "key", "summary": "summary", "issue type": "issuetype", "status": "status",
    "priority": "priority", "resolution": "resolution", "assignee": "assignee", "reporter": "reporter",
    "created": "created", "updated": "updated", "description": "description", "environment": "environment",
    "labels": "labels", "component/s": "components", "fix version/s": "fixVersions", "sprint": "sprint",
    "comment": "comments", "parent": "parent", "parent id": "parent",
}
_MULTI = {"labels", "components", "fixVersions", "comments"}
_CSV_COMMENT = re.compile(r"^(\d{1,2}/\w{3}/\d{2,4}[^;]*);([^;]*);(.*)$", re.S)


def issues_from_csv(text: str) -> list[dict]:
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    header = [h.strip().lower() for h in rows[0]]
    issues = []
    for row in rows[1:]:
        values: dict[str, list[str]] = defaultdict(list)
        for name, value in zip(header, row):
            target = _CSV_FIELDS.get(name)
            if target and value.strip():
                values[target].append(value.strip())
        if not values.get("key"):
            continue
        issue: dict = {}
        for target, items in values.items():
            if target == "comments":
                issue["comments"] = [_csv_comment(c) for c in items]
            elif target in _MULTI:
                issue[target] = items
            else:
                issue[target] = items[0]
        issues.append(issue)
    return issues


def _csv_comment(value: str) -> dict:
    match = _CSV_COMMENT.match(value)
    if match:
        return {"created": match.group(1).strip(), "author": "", "body": match.group(3).strip()}
    return {"created": "", "author": "", "body": value}


# --- Jira printable / Word view saved as text ------------------------------------------

_TEXT_HEADER = re.compile(
    r"^\[(?P<key>[A-Z][A-Z0-9_]+-\d+)\]\s+(?P<summary>.+?)"
    r"(?:\s+Created:\s*(?P<created>\S+))?(?:\s+Updated:\s*(?P<updated>\S+))?\s*$",
    re.M,
)
_TEXT_FIELD = re.compile(r"([A-Z][A-Za-z /]+?):\t+([^\t]*)")
_TEXT_COMMENT = re.compile(r"^Comment by (?P<who>.+?) \[ (?P<when>[^\]]+) \]\s*$")
_EMPTY = {"none", "not specified", ""}


def issues_from_text(text: str) -> list[dict]:
    if re.search(r"<(html|body|table|div)\b", text, re.I):
        text = _html_to_text(text)
    text = normalize(text, keep_tabs=True)
    headers = list(_TEXT_HEADER.finditer(text))
    issues = []
    for i, header in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        issues.append(_text_issue(header, text[header.end() : end]))
    return issues


def _text_issue(header: re.Match, body: str) -> dict:
    fields: dict[str, str] = {}
    description: list[str] = []
    comments: list[dict] = []
    mode = "fields"
    for line in body.split("\n"):
        stripped = line.strip()
        if stripped.startswith("Generated at ") and "using Jira" in stripped:
            break
        if stripped == "Back to previous view":
            continue
        if mode == "fields" and stripped.lower() == "description":
            mode = "description"
            continue
        if mode in ("fields", "description") and stripped.lower() == "comments":
            mode = "comments"
            continue
        if mode == "fields":
            for name, value in _TEXT_FIELD.findall(line + "\t"):
                fields[name.strip().lower()] = value.strip()
        elif mode == "description":
            description.append(line.replace("\t", " "))
        else:
            match = _TEXT_COMMENT.match(stripped)
            if match:
                comments.append({"author": match.group("who"), "created": _date(match.group("when")), "body": ""})
            elif comments:
                comments[-1]["body"] += line.replace("\t", " ") + "\n"

    def value(name: str) -> str:
        v = fields.get(name, "")
        return "" if v.lower() in _EMPTY else v

    def items(name: str) -> list[str]:
        return [x.strip() for x in value(name).split(",") if x.strip()]

    return {
        "key": header.group("key"),
        "summary": header.group("summary").strip(),
        "created": header.group("created") or "",
        "updated": header.group("updated") or "",
        "project": value("project"),
        "status": value("status"),
        "issuetype": value("type"),
        "priority": value("priority"),
        "resolution": value("resolution"),
        "assignee": value("assignee"),
        "reporter": value("reporter"),
        "labels": items("labels"),
        "components": items("components"),
        "fixVersions": items("fix versions"),
        "sprint": value("sprint"),
        "description": "\n".join(description).strip(),
        "comments": [c for c in comments if c["body"].strip()],
    }


def _html_to_text(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style).*?</\1>", "", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", markup)
    markup = re.sub(r"(?i)</t[dh]>", "\t", markup)
    return html.unescape(re.sub(r"<[^>]+>", "", markup))


# --- Chunks -------------------------------------------------------------------------


def ticket_chunks(ctx: LoadContext, issue: dict) -> list[Chunk]:
    key = issue["key"]
    line1 = " · ".join(
        x for x in (
            f"Jira {key}", issue["type"], issue["status"] and f"Status {issue['status']}",
            issue["priority"] and f"Priority {issue['priority']}", issue["project"] and f"Project {issue['project']}",
        ) if x
    )
    head = [line1, f"Summary: {issue['summary']}"]
    tags = " | ".join(
        x for x in (
            issue["components"] and "Components: " + ", ".join(issue["components"]),
            issue["labels"] and "Labels: " + ", ".join(issue["labels"]),
            issue["fix_versions"] and "Fix versions: " + ", ".join(issue["fix_versions"]),
            issue["sprint"] and f"Sprint: {issue['sprint']}",
        ) if x
    )
    if tags:
        head.append(tags)
    people = " · ".join(
        x for x in (
            issue["reporter"] and f"Reporter: {issue['reporter']}",
            issue["assignee"] and f"Assignee: {issue['assignee']}",
            issue["created"] and f"Created: {issue['created']}",
            issue["updated"] and f"Updated: {issue['updated']}",
            issue["resolution"] and f"Resolution: {issue['resolution']}",
        ) if x
    )
    if people:
        head.append(people)
    if issue["parent"] or issue["links"]:
        head.append("Links: " + ", ".join(([f"parent {issue['parent']}"] if issue["parent"] else []) + issue["links"]))
    header = "\n".join(head)

    body_parts = []
    if issue["environment"].strip():
        body_parts.append("Environment:\n" + issue["environment"].strip())
    if issue["description"].strip():
        body_parts.append("Description:\n" + issue["description"].strip())
    if issue["comments"]:
        lines = []
        for c in issue["comments"]:
            who = " · ".join(x for x in (c["created"], c["author"]) if x)
            lines.append(f"- {who + ': ' if who else ''}{c['body'].strip()}")
        body_parts.append("Comments:\n" + "\n".join(lines))
    body = mask_secrets(normalize("\n\n".join(body_parts)))

    chunking = ctx.source.chunking
    budget = chunking.max_tokens - estimate_tokens(header)
    parts = split_text(body, budget, chunking.overlap_tokens) if body else [""]
    title = f"{key}: {issue['summary']}"[:200]
    chunks = []
    for i, part in enumerate(parts, 1):
        suffix = f"\n(part {i} of {len(parts)})" if len(parts) > 1 else ""
        chunks.append(
            ctx.chunk(
                f"{header}{suffix}\n\n{part}".strip(),
                title=title,
                location=issue["status"] or "ticket",
                url=issue["url"],
                source_type="jira",
                key=key,
                type=issue["type"],
                status=issue["status"],
                priority=issue["priority"],
                labels=issue["labels"],
                components=issue["components"],
                created=issue["created"],
                updated=issue["updated"],
            )
        )
    return chunks
