import dataclasses
import datetime as dt
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from qabuddy import auto_ingest, ingest, jira_mcp
from qabuddy.auto_ingest import UP_TO_DATE, AutoIngest, RefreshReport, Step, changes, pull_repositories, run_refresh, sync_jira_step
from qabuddy.ingest import IngestBusy, IngestReport, SourceReport, ingest_lock
from qabuddy.settings import get_settings
from qabuddy.sources import Chunking, Source

APP_DIR = Path(__file__).resolve().parent.parent
QUIET = lambda message: None  # noqa: E731


@pytest.fixture
def hourly(settings):
    return dataclasses.replace(settings, auto_ingest_minutes=60, snapshot_dir=None, jira_mcp_url="", jira_mcp_command="", jira_jql="")


def fake_ingest_report(indexed=0, removed=(), errors=()):
    report = IngestReport(started="2026-10-10T10:00:00")
    report.sources["test_cases"] = SourceReport("Test cases", "00_TestCases", indexed=indexed)
    report.removed, report.errors = list(removed), list(errors)
    return report


def fake_refresh(indexed=0, removed=(), steps=()):
    report = fake_ingest_report(indexed, removed)
    return RefreshReport(steps=[*steps, Step("ingest", "data folders", "ok", changes(report))], ingest=report)


# --- The lock ---------------------------------------------------------------------------


def test_the_lock_lets_one_ingestion_run_at_a_time(hourly):
    with ingest_lock(hourly):
        with pytest.raises(IngestBusy):
            with ingest_lock(hourly):
                pass
    with ingest_lock(hourly):  # released again
        pass


def test_the_lock_works_across_processes_and_dies_with_its_owner(hourly):
    code = "\n".join([
        "import dataclasses, pathlib, sys, time",
        f"sys.path.insert(0, {str(APP_DIR)!r})",
        "from qabuddy.ingest import ingest_lock",
        "from qabuddy.settings import get_settings",
        f"settings = dataclasses.replace(get_settings(), storage_dir=pathlib.Path({str(hourly.storage_dir)!r}))",
        "with ingest_lock(settings):",
        "    print('locked', flush=True)",
        "    time.sleep(60)",
    ])
    holder = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "locked"
        with pytest.raises(IngestBusy):
            with ingest_lock(hourly):
                pass
    finally:
        holder.kill()
        holder.wait(10)
    for _ in range(50):  # the OS releases a dead process's locks, on Windows not always instantly
        try:
            with ingest_lock(hourly):
                return
        except IngestBusy:
            time.sleep(0.1)
    pytest.fail("the lock outlived the process that held it")


# --- One refresh ------------------------------------------------------------------------


def test_summary_and_status_of_a_refresh():
    pulled = Step("git pull", "ATB13xSeleniumAdvanceFramework", "ok", "2 new commits (abc1234..def5678)")
    same = Step("git pull", "AdvancePlaywrightFramework1x", "ok", UP_TO_DATE)
    report = fake_refresh(indexed=1, steps=[pulled, same])
    assert report.status == "ok"
    assert report.summary == "ATB13xSeleniumAdvanceFramework: 2 new commits (abc1234..def5678); 1 file indexed"

    jira_down = Step("jira sync", "project = VWO", "error", "ConnectError: refused")
    report = fake_refresh(steps=[jira_down])
    assert report.status == "warning"
    assert report.summary == "no changes; jira sync failed (project = VWO): ConnectError: refused"

    assert fake_refresh(indexed=3, removed=["a.md"]).summary == "3 files indexed, 1 removed"
    assert RefreshReport([Step("ingest", "data folders", "error", "RuntimeError: Qdrant down")]).status == "error"


def test_refresh_pulls_syncs_jira_then_ingests_under_one_lock(hourly, monkeypatch):
    calls = []
    monkeypatch.setattr(auto_ingest, "pull_repositories", lambda settings, log=print: calls.append("git pull") or [])
    monkeypatch.setattr(auto_ingest, "sync_jira_step", lambda settings, log=print: calls.append("jira sync") or None)

    def fake_run_ingest(settings, log=print, lock=True):
        calls.append(f"ingest lock={lock}")
        with pytest.raises(IngestBusy):  # the refresh holds the lock for the whole run
            with ingest_lock(settings):
                pass
        return fake_ingest_report(indexed=1)

    monkeypatch.setattr(ingest, "run_ingest", fake_run_ingest)
    report = run_refresh(hourly, log=QUIET)
    assert calls == ["git pull", "jira sync", "ingest lock=False"]
    assert (report.status, report.summary) == ("ok", "1 file indexed")


def test_refresh_records_an_ingest_failure(hourly, monkeypatch):
    monkeypatch.setattr(auto_ingest, "pull_repositories", lambda settings, log=print: [])

    def qdrant_down(settings, log=print, lock=True):
        raise RuntimeError("Qdrant: Qdrant not reachable at http://127.0.0.1:6333")

    monkeypatch.setattr(ingest, "run_ingest", qdrant_down)
    report = run_refresh(hourly, log=QUIET)
    assert report.status == "error" and report.ingest is None
    assert "Qdrant not reachable" in report.summary


def test_jira_sync_runs_only_when_configured(hourly, monkeypatch):
    assert sync_jira_step(hourly, QUIET) is None
    configured = dataclasses.replace(hourly, jira_mcp_url="http://mcp-atlassian:9000/mcp", jira_jql="project = VWO")
    monkeypatch.setattr(jira_mcp, "sync_jira", lambda settings, jql: jira_mcp.SyncResult("jira_search", 3, 3, 0))
    step = sync_jira_step(configured, QUIET)
    assert (step.status, step.detail) == ("ok", "3 tickets fetched, 3 written")

    def unreachable(settings, jql):
        raise ConnectionError("refused")

    monkeypatch.setattr(jira_mcp, "sync_jira", unreachable)
    assert sync_jira_step(configured, QUIET).status == "error"


def _git(*args):
    subprocess.run(["git", "-c", "user.name=QABuddy", "-c", "user.email=qabuddy@example.com", *args], check=True, capture_output=True)


def _commit(repo: Path, name: str, text: str):
    (repo / name).write_text(text, encoding="utf-8")
    _git("-C", str(repo), "add", name)
    _git("-C", str(repo), "commit", "-q", "-m", f"add {name}")


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_git_pull_brings_new_commits_into_a_source_repository(hourly, tmp_path):
    upstream, clone = tmp_path / "upstream", tmp_path / "08_Source_Codes" / "framework"
    _git("init", "-q", "-b", "main", str(upstream))
    _commit(upstream, "README.md", "framework")
    _git("clone", "-q", str(upstream), str(clone))
    _commit(upstream, "LoginTest.java", "class LoginTest {}")
    _commit(upstream, "CartTest.java", "class CartTest {}")
    source = Source("selenium", "Selenium framework", clone, "code", 1, Chunking(800, 80, 80))

    [step] = pull_repositories(hourly, [source], QUIET)
    assert step.status == "ok" and step.detail.startswith("2 new commits")
    assert (clone / "CartTest.java").exists()
    [step] = pull_repositories(hourly, [source], QUIET)
    assert step.detail == UP_TO_DATE

    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    assert pull_repositories(hourly, [dataclasses.replace(source, folder=plain)], QUIET) == []
    assert pull_repositories(dataclasses.replace(hourly, auto_git_pull=False), [source], QUIET) == []


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_failed_pull_is_reported_not_raised(hourly, tmp_path):
    clone = tmp_path / "framework"
    _git("init", "-q", "-b", "main", str(clone))
    _commit(clone, "README.md", "no remote to pull from")
    source = Source("selenium", "Selenium framework", clone, "code", 1, Chunking(800, 80, 80))
    [step] = pull_repositories(hourly, [source], QUIET)
    assert step.status == "error" and step.detail


# --- The schedule -----------------------------------------------------------------------


def test_off_unless_turned_on(monkeypatch):
    monkeypatch.delenv("QABUDDY_AUTO_INGEST_MINUTES", raising=False)
    assert get_settings.__wrapped__().auto_ingest_minutes == 0
    monkeypatch.setenv("QABUDDY_AUTO_INGEST_MINUTES", "60")
    assert get_settings.__wrapped__().auto_ingest_minutes == 60


def test_turned_off_by_zero_minutes_or_a_snapshot(hourly, tmp_path):
    off = AutoIngest(dataclasses.replace(hourly, auto_ingest_minutes=0))
    assert off.status() == {"enabled": False, "reason": "off: set QABUDDY_AUTO_INGEST_MINUTES=60 to re-index every hour"}
    assert off.start() is False
    serverless = AutoIngest(dataclasses.replace(hourly, snapshot_dir=tmp_path))
    assert serverless.status()["enabled"] is False and "snapshot" in serverless.status()["reason"]
    assert serverless.start() is False


def test_a_run_is_recorded_and_survives_a_restart(hourly):
    scheduler = AutoIngest(hourly, job=lambda settings, log: fake_refresh(indexed=2, removed=["03_Meeting_Notes/old.md"]))
    record = scheduler.run_once()
    assert record["status"] == "ok" and record["summary"] == "2 files indexed, 1 removed"
    assert record["steps"][-1]["name"] == "ingest" and record["seconds"] >= 0
    assert (hourly.storage_dir / "auto_ingest.json").exists()
    assert AutoIngest(hourly).status()["last_run"]["summary"] == "2 files indexed, 1 removed"


def test_a_busy_lock_skips_a_run_and_a_failure_is_recorded(hourly):
    def busy(settings, log):
        raise IngestBusy("Another ingestion is running")

    def broken(settings, log):
        raise RuntimeError("Qdrant: not reachable")

    assert AutoIngest(hourly, job=busy).run_once()["status"] == "skipped"
    record = AutoIngest(hourly, job=broken).run_once()
    assert record["status"] == "error" and "Qdrant" in record["summary"]


def test_the_schedule_repeats_until_stopped(hourly):
    runs, third = [], threading.Event()

    def job(settings, log):
        runs.append(time.monotonic())
        if len(runs) == 3:
            third.set()
        return fake_refresh()

    scheduler = AutoIngest(hourly, job=job, interval=0.1, first_delay=0.01)
    assert scheduler.start() and not scheduler.start()  # one thread only
    assert third.wait(5)
    scheduler.stop()
    count = len(runs)
    time.sleep(0.3)
    assert len(runs) == count
    # Runs keep a fixed cadence, so a late start (Windows timers tick every ~16 ms) shortens the
    # next gap a little; back-to-back runs would be ~0 s apart.
    assert min(b - a for a, b in zip(runs, runs[1:])) >= 0.05
    assert scheduler.last["status"] == "ok"


def test_next_run_is_shown_while_waiting(hourly):
    scheduler = AutoIngest(hourly, job=lambda settings, log: fake_refresh(), first_delay=3600)
    assert scheduler.status()["next_run"] is None  # not started
    scheduler.start()
    try:
        status = scheduler.status()
        assert status["enabled"] and not status["running"] and status["every_minutes"] == 60
        assert dt.datetime.fromisoformat(status["next_run"]) > dt.datetime.now()
    finally:
        scheduler.stop()
