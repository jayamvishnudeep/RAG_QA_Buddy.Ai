"""A stand-in Jira MCP server for tests: the same tool name and arguments as mcp-atlassian."""

import json

from mcp.server.mcpserver import MCPServer

ISSUES = [
    {
        "key": f"FAKE-{n}",
        "summary": f"Fake ticket {n}",
        "status": {"name": "Open" if n % 2 else "Done"},
        "issue_type": {"name": "Bug"},
        "priority": {"name": "Medium"},
        "labels": ["mcp-test"],
        "created": "2026-04-0{0}T10:00:00.000+0000".format(n),
        "description": f"Description of fake ticket {n}.",
        "comments": [{"author": {"display_name": "Tester"}, "created": "2026-04-09", "body": f"Comment on {n}"}],
    }
    for n in range(1, 6)
]

server = MCPServer("fake-jira")


@server.tool()
def jira_search(jql: str, fields: str = "", limit: int = 10, start_at: int = 0) -> str:
    """Search Jira issues using JQL."""
    page = ISSUES[start_at : start_at + limit]
    return json.dumps({"total": len(ISSUES), "start_at": start_at, "max_results": limit, "issues": page})


if __name__ == "__main__":
    server.run("stdio")
