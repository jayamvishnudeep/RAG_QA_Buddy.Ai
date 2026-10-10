"""Hourly auto-ingestion: keeps the index in step with the data folders while the app runs.

It is off until QABUDDY_AUTO_INGEST_MINUTES is set (60 = hourly). Then the web app
runs one refresh every N minutes in a background thread; the first run starts 30
seconds after launch, to pick up whatever changed while the app was stopped. The
same refresh can run from cron instead: ``python -m qabuddy refresh`` (see
deploy/qabuddy.cron). A refresh is:

1. ``git pull --ff-only`` in each source repository, so new commits get indexed
   (QABUDDY_AUTO_GIT_PULL=false skips it);
2. the Jira MCP sync, when JIRA_JQL and a Jira MCP server are configured;
3. an incremental ingest: only new or changed files are embedded, deleted ones are dropped.

The whole refresh holds the ingestion lock, so a slow run, the next one and a
manual ``python -m qabuddy ingest`` never overlap. The last run is saved to
storage/auto_ingest.json and shown in the sidebar.

The ingestion code (Qdrant client, parsers) is imported only when a run starts:
the serverless build imports this module but never runs it.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from .settings import Settings
from .sources import Source, load_catalog

if TYPE_CHECKING:
    from .ingest import IngestReport

START_DELAY = 30.0  # seconds between the app starting and the first run
GIT_TIMEOUT = 60  # seconds per git command; a fetch from GitHub normally takes 1-3 s
UP_TO_DATE = "already up to date"

Log = Callable[[str], None]


@dataclass
class Step:
    name: str  # git pull | jira sync | ingest
    target: str
    status: str  # ok | skipped | error
    detail: str


@dataclass
class RefreshReport:
    steps: list[Step] = field(default_factory=list)
    ingest: IngestReport | None = None

    @property
    def status(self) -> str:
        if self.ingest is None:
            return "error"
        if self.ingest.errors or any(s.status == "error" for s in self.steps):
            return "warning"
        return "ok"

    @property
    def summary(self) -> str:
        parts = [f"{s.target}: {s.detail}" for s in self.steps if s.name == "git pull" and s.status == "ok" and s.detail != UP_TO_DATE]
        parts.append(changes(self.ingest) if self.ingest else "index not updated")
        parts += [f"{s.name} failed ({s.target}): {s.detail}" for s in self.steps if s.status == "error"]
        return "; ".join(parts)


def changes(report: IngestReport) -> str:
    indexed = sum(r.indexed for r in report.sources.values())
    parts = [f"{indexed} file{'' if indexed == 1 else 's'} indexed"] if indexed else []
    if report.removed:
        parts.append(f"{len(report.removed)} removed")
    if report.errors:
        parts.append(f"{len(report.errors)} failed")
    return ", ".join(parts) or "no changes"


# --- One refresh ------------------------------------------------------------------------


def run_refresh(settings: Settings, log: Log = print) -> RefreshReport:
    """Pull the repositories, sync Jira, then ingest what changed.

    Raises IngestBusy when another ingestion holds the lock.
    """
    from .ingest import ingest_lock, run_ingest

    report = RefreshReport()
    with ingest_lock(settings):
        report.steps += pull_repositories(settings, log=log)
        jira = sync_jira_step(settings, log)
        if jira:
            report.steps.append(jira)
        try:
            report.ingest = run_ingest(settings, log=log, lock=False)
        except Exception as error:  # Qdrant or Ollama down: record it and try again next time
            report.steps.append(Step("ingest", "data folders", "error", _short(error, 300)))
        else:
            report.steps.append(Step("ingest", "data folders", "ok", changes(report.ingest)))
    return report


def pull_repositories(settings: Settings, sources: list[Source] | None = None, log: Log = print) -> list[Step]:
    """``git pull --ff-only`` in every source repository, so new commits reach the index."""
    if not settings.auto_git_pull:
        return []
    git = shutil.which("git")
    steps = []
    for source in load_catalog(settings).sources if sources is None else sources:
        if source.loader != "code" or not source.active or not (source.folder / ".git").exists():
            continue
        name = source.folder.name
        if not git:
            step = Step("git pull", name, "skipped", "git is not installed")
        elif not os.access(source.folder, os.W_OK):
            step = Step("git pull", name, "skipped", "the folder is read-only")
        else:
            step = _pull(git, source.folder, name)
        log(f"git pull {name}: {step.status}, {step.detail}")
        steps.append(step)
    return steps


def _pull(git: str, folder: Path, name: str) -> Step:
    try:
        before = _git(git, folder, "rev-parse", "HEAD")
        # A stalled download gives up after 20 s below 1 KB/s instead of waiting for the timeout.
        _git(git, folder, "-c", "http.lowSpeedLimit=1000", "-c", "http.lowSpeedTime=20", "pull", "--ff-only", "--quiet")
        after = _git(git, folder, "rev-parse", "HEAD")
        if before == after:
            return Step("git pull", name, "ok", UP_TO_DATE)
        count = int(_git(git, folder, "rev-list", "--count", f"{before}..{after}"))
    except subprocess.TimeoutExpired:
        return Step("git pull", name, "error", f"timed out after {GIT_TIMEOUT} s")
    except subprocess.CalledProcessError as error:  # no network, diverged branch, no upstream
        lines = (error.stderr or error.stdout or "").strip().splitlines()
        return Step("git pull", name, "error", (lines[-1] if lines else f"git exited with {error.returncode}")[:200])
    return Step("git pull", name, "ok", f"{count} new commit{'' if count == 1 else 's'} ({before[:7]}..{after[:7]})")


def _git(git: str, folder: Path, *args: str) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}  # never wait for a password
    result = subprocess.run(
        [git, "-C", str(folder), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=GIT_TIMEOUT,
        env=env,
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return result.stdout.strip()


def sync_jira_step(settings: Settings, log: Log = print) -> Step | None:
    """The Jira MCP sync, when a server and a JQL query are configured."""
    if not (settings.jira_mcp_configured and settings.jira_jql):
        return None
    from .jira_mcp import sync_jira

    try:
        result = sync_jira(settings, settings.jira_jql)
    except Exception as error:  # Jira down must not stop the files from being indexed
        step = Step("jira sync", settings.jira_jql, "error", _short(error))
    else:
        step = Step("jira sync", settings.jira_jql, "ok", f"{result.fetched} tickets fetched, {result.written} written")
    log(f"jira sync: {step.status}, {step.detail}")
    return step


def _short(error: Exception, limit: int = 200) -> str:
    return f"{type(error).__name__}: {error}"[:limit]


# --- The schedule -----------------------------------------------------------------------


class AutoIngest:
    """Runs a refresh every N minutes in a daemon thread of the web app."""

    def __init__(
        self,
        settings: Settings,
        job: Callable[..., RefreshReport] = run_refresh,
        interval: float | None = None,
        first_delay: float = START_DELAY,
    ):
        self.settings = settings
        self.interval = settings.auto_ingest_minutes * 60 if interval is None else interval
        self.first_delay = first_delay
        self.running = False
        self.last: dict | None = self._load()
        self._job = job
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._due: float | None = None  # time.monotonic() of the next run

    @property
    def disabled_reason(self) -> str:
        if self.settings.snapshot_dir:
            return "serving a read-only index snapshot"
        if self.interval <= 0:
            return "off: set QABUDDY_AUTO_INGEST_MINUTES=60 to re-index every hour"
        return ""

    def start(self) -> bool:
        if self.disabled_reason or self._thread:
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="auto-ingest", daemon=True)
        self._thread.start()
        _log(f"every {self.interval / 60:g} min; first run in {self.first_delay:g} s")
        return True

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)  # a run in progress keeps its saved progress if it is cut off
            self._thread = None

    def status(self) -> dict:
        if self.disabled_reason:
            return {"enabled": False, "reason": self.disabled_reason}
        next_run = None
        if self._thread and self._due is not None and not self.running:
            next_run = _iso(time.time() + max(0.0, self._due - time.monotonic()))
        return {
            "enabled": True,
            "every_minutes": round(self.interval / 60, 2),
            "running": self.running,
            "next_run": next_run,
            "last_run": self.last,
        }

    def run_once(self) -> dict:
        from .ingest import IngestBusy

        started = time.time()
        record: dict = {"started": _iso(started)}
        self.running = True
        try:
            report = self._job(self.settings, log=_log)
        except IngestBusy as busy:
            record.update(status="skipped", summary=str(busy), steps=[])
        except Exception as error:  # the schedule must survive anything a run throws
            record.update(status="error", summary=_short(error, 300), steps=[])
        else:
            record.update(status=report.status, summary=report.summary, steps=[asdict(s) for s in report.steps])
        finally:
            self.running = False
        record.update(finished=_iso(time.time()), seconds=round(time.time() - started, 1))
        self.last = record
        self._save(record)
        _log(f"{record['status']}: {record['summary']} ({record['seconds']} s)")
        return record

    def _loop(self) -> None:
        due = time.monotonic() + self.first_delay
        while True:
            self._due = due
            if self._stop.wait(max(0.0, due - time.monotonic())):
                return
            self.run_once()
            due += self.interval
            late = time.monotonic() - due
            if late >= 0:  # the run took longer than the interval: skip the slots it overran
                due += (late // self.interval + 1) * self.interval

    @property
    def _path(self) -> Path:
        return self.settings.storage_dir / "auto_ingest.json"

    def _load(self) -> dict | None:
        try:
            return json.loads(self._path.read_text(encoding="utf-8")).get("last_run")
        except (OSError, ValueError, AttributeError):
            return None

    def _save(self, record: dict) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"last_run": record}, indent=1), encoding="utf-8")
            tmp.replace(self._path)
        except OSError as error:
            _log(f"could not save {self._path.name}: {error}")


def _iso(timestamp: float) -> str:
    return dt.datetime.fromtimestamp(timestamp).isoformat(timespec="seconds")


def _log(message: str) -> None:
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} auto-ingest: {message}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:  # a console or log file that is not UTF-8
        print(line.encode("ascii", "replace").decode("ascii"), flush=True)
