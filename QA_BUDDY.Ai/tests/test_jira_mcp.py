import dataclasses
import json
import sys
from pathlib import Path

from qabuddy.jira_mcp import split_args, sync_jira
from qabuddy.loaders import jira
from qabuddy.loaders.base import LoadContext
from qabuddy.sources import Chunking, Source

SERVER = Path(__file__).resolve().parent / "fake_jira_mcp_server.py"


def test_split_args_keeps_windows_paths():
    assert split_args('"C:\\Program Files\\x\\server.py" --port 9000') == ["C:\\Program Files\\x\\server.py", "--port", "9000"]


def test_sync_over_stdio_paginates_and_snapshots(settings):
    s = dataclasses.replace(
        settings, jira_mcp_command=sys.executable, jira_mcp_args=f'"{SERVER}"', jira_mcp_url="", jira_page_size=2
    )
    result = sync_jira(s, "project = FAKE ORDER BY created")
    assert result.tool == "jira_search"
    assert result.fetched == 5 and result.written == 5  # three pages of 2, 2 and 1
    files = sorted(p.name for p in s.jira_snapshot_dir.glob("*.json"))
    assert files == [f"FAKE-{n}.json" for n in range(1, 6)]

    again = sync_jira(s, "project = FAKE")
    assert again.written == 0  # unchanged tickets are not rewritten

    snapshot = s.jira_snapshot_dir / "FAKE-1.json"
    source = Source("jira", "Jira tickets", s.jira_snapshot_dir, "jira", 1, Chunking(700, 80))
    chunks = jira.load(LoadContext(source, snapshot, "jira-mcp/FAKE-1.json", "jira-mcp/FAKE-1.json", s))
    assert chunks[0].meta["key"] == "FAKE-1"
    assert "Description of fake ticket 1." in chunks[0].text
    assert "Tester: Comment on 1" in chunks[0].text
    assert json.loads(snapshot.read_text(encoding="utf-8"))["key"] == "FAKE-1"
