"""Jenkins console logs and test results (JUnit, TestNG, Playwright JSON).

Raw build logs are mostly noise. Each log is cleaned (ANSI codes, Jenkins
annotations, download progress, repeated lines) and turned into three kinds of
chunk:

- a **build summary**: job, build, result, stages, test counts, failing tests;
- **failure windows**: each error with the lines around it (stack traces kept);
- **stage windows**: the rest of the log by pipeline stage, for completeness.

Test reports become one summary per report plus one chunk per failed or flaky
test. Summaries list every test with its status, so the history of a test
across builds (flaky or not) can be retrieved later.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from ..chunking import split_lines
from ..models import Chunk
from ..text import CODE_CHARS_PER_TOKEN, estimate_tokens, mask_secrets, normalize, strip_ansi
from .base import LoadContext, Unsupported, read_text

CONSOLE_SUFFIXES = {".log", ".txt", ".out", ""}

_JENKINS_NOTE = re.compile(r"\x1b\[8mha:.*?\x1b\[0m", re.S)
# Jenkins' Timestamper prefix: [2026-04-05T02:11:04.112Z]
_TIMESTAMP = re.compile(r"^\[(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.\d+)?Z\] ?", re.M)
_NOISE = re.compile(
    r"^\s*(\[Pipeline\] (?!\{ \().*"  # pipeline step markers (stage starts are kept)
    r"|(\[INFO\] )?Download(ing|ed) from .*"
    r"|Progress \(\d+\).*"
    r"|\s*\d+(\.\d+)? ?[KMG]?B\s*/\s*\d+.*"
    r"|npm (WARN|notice) .*"
    r"|\[INFO\] -+"
    r"|\s*)$"
)
_STAGE = re.compile(r"^\[Pipeline\] \{ \((.+)\)$")
_ERROR = re.compile(
    r"(\bERROR\b|\[ERROR\]|\bFAILED\b|\bFAILURE\b|BUILD FAILURE|Exception\b|^\s*Error: |AssertionError|"
    r"Assertion(Failed)?Error|\bTimeoutError\b|Timeout \d+ms exceeded|npm ERR!|✘|✗|\bFATAL\b|"
    r"script returned exit code [1-9]|exit code [1-9]\d*|Caused by:|Tests? failed)"
)
_STACK = re.compile(r"^\s+at [\w$.<>]+\(.*\)$|^\s+at .+:\d+:\d+\)?$")
_RESULT = re.compile(r"^Finished: (SUCCESS|FAILURE|UNSTABLE|ABORTED|NOT_BUILT)", re.M)
_SUREFIRE = re.compile(r"Tests run: (\d+), Failures: (\d+), Errors: (\d+), Skipped: (\d+)")
_PLAYWRIGHT_COUNT = re.compile(r"^\s*(\d+) (passed|failed|flaky|skipped|did not run)\b", re.M)
_FAILED_TEST = re.compile(
    r"(^\s*\d+\) \[[\w-]+\] › .+$"  # Playwright: 1) [chromium] › tests/login.spec.ts:12:5 › ...
    r"|^\[ERROR\]\s{2,}\S+[.:]\S+.*$"  # Maven/surefire failure list
    r"|^FAILED: .+$)",  # TestNG console
    re.M,
)
_RETRIED_TEST = re.compile(r"^(SKIPPED: .+|.*\bRetrying\b.+)$", re.M)  # TestNG marks a retried attempt SKIPPED
_PLAYWRIGHT_GROUP = re.compile(r"^\s*\d+ (failed|flaky)\s*$")
_PLAYWRIGHT_TEST = re.compile(r"^\s+\[[\w-]+\] › .+$")
_JOB_BUILD = re.compile(r"^(?P<job>.+?)[\s_#\-]+(?:build[\s_#\-]*)?#?(?P<build>\d+)$", re.I)


def load(ctx: LoadContext) -> list[Chunk]:
    suffix = ctx.path.suffix.lower()
    job, build = _job_and_build(ctx)
    if suffix == ".xml":
        return _xml_report(ctx, job, build)
    if suffix == ".json":
        return _playwright_report(ctx, job, build)
    if suffix in CONSOLE_SUFFIXES or ctx.path.name.lower() in ("consoletext", "console"):
        return _console(ctx, job, build)
    raise Unsupported(f"{suffix} is not a build log or test report")


def _job_and_build(ctx: LoadContext) -> tuple[str, str]:
    """Guess job and build number from paths like JOB/142/console.log or JOB_142.log."""
    parts = Path(ctx.rel_path).parts
    stem = re.sub(r"^TEST-(TestSuite[_-])?", "", Path(ctx.rel_path).stem)  # surefire's TEST-<suite>.xml naming
    match = _JOB_BUILD.match(stem)
    if match:
        return match.group("job"), match.group("build")
    for i in range(len(parts) - 2, -1, -1):
        if re.fullmatch(r"#?\d+", parts[i]):
            return (parts[i - 1] if i > 0 else stem), parts[i].lstrip("#")
    return (parts[0] if len(parts) > 1 else stem), ""


def _label(job: str, build: str) -> str:
    return f"{job} #{build}" if build else job


# --- Console logs -------------------------------------------------------------------


def clean_log(raw: str) -> list[str]:
    text = _TIMESTAMP.sub("", normalize(strip_ansi(_JENKINS_NOTE.sub("", raw))))
    lines: list[str] = []
    repeats = 0
    for line in text.split("\n"):
        if _NOISE.match(line):
            continue
        if lines and line == lines[-1]:
            repeats += 1
            continue
        if repeats:
            lines.append(f"... (previous line repeated {repeats} more times)")
            repeats = 0
        lines.append(line)
    if repeats:
        lines.append(f"... (previous line repeated {repeats} more times)")
    return lines


def _console(ctx: LoadContext, job: str, build: str) -> list[Chunk]:
    raw = read_text(ctx.path)
    lines = [mask_secrets(l, inline=True) for l in clean_log(raw)]
    if not lines:
        return []
    text = "\n".join(lines)
    workspace = re.search(r"in workspace \S*/workspace/([^/\s]+)", raw)
    if workspace and not build:
        job = workspace.group(1)
    label = _label(job, build)
    result_match = _RESULT.search(raw)
    result = result_match.group(1) if result_match else ""
    chunking = ctx.source.chunking
    stamps = _TIMESTAMP.findall(raw)
    ran = f"{stamps[0][0]} {stamps[0][1]}-{stamps[-1][1]} UTC" if stamps else ""
    head = f"Jenkins build {label}" + (f" · result {result}" if result else "") + (f" · ran {ran}" if ran else "")

    # Stages, with where each starts.
    stages: list[tuple[str, int]] = [("(start)", 0)]
    for i, line in enumerate(lines):
        match = _STAGE.match(line)
        if match:
            stages.append((match.group(1), i))
    error_lines = [i for i, line in enumerate(lines) if _ERROR.search(line)]
    failed_stages = []
    for n, (name, start) in enumerate(stages):
        end = stages[n + 1][1] if n + 1 < len(stages) else len(lines)
        if any(start <= i < end for i in error_lines) and name != "(start)":
            failed_stages.append(name)

    tests = _test_counts(text)
    failing = list(dict.fromkeys(m.group(0).strip() for m in _FAILED_TEST.finditer(text)))[:30]
    retried = list(dict.fromkeys(m.group(0).strip() for m in _RETRIED_TEST.finditer(text)))[:15]
    groups = _playwright_groups(lines)
    revision = re.search(r"Checking out Revision ([0-9a-f]{7,40})(?: \((.+?)\))?", raw)
    commit_message = re.search(r'^Commit message: "?(.+?)"?$', text, re.M)
    started = re.search(r"^Started by (.+)$", text, re.M)
    agent = re.search(r"Building (?:remotely )?on (\S+)", text)

    summary = [head]
    if started:
        summary.append(f"Started by {started.group(1).strip()}" + (f" · agent {agent.group(1)}" if agent else ""))
    if revision:
        summary.append(
            f"Commit {revision.group(1)[:12]}" + (f" ({revision.group(2)})" if revision.group(2) else "")
            + (f": {commit_message.group(1)}" if commit_message else "")
        )
    named_stages = [s for s, _ in stages if s != "(start)"]
    if named_stages:
        summary.append("Stages: " + ", ".join(f"{s} (errors)" if s in failed_stages else s for s in named_stages))
    if tests:
        summary.append("Tests: " + ", ".join(f"{v} {k}" for k, v in tests.items()))
    for group, names in groups.items():
        summary.append(f"{group.capitalize()} tests (Playwright):\n" + "\n".join(f"- {n}" for n in names))
    if failing:
        summary.append("Failing tests:\n" + "\n".join(f"- {f}" for f in failing))
    if retried:
        summary.append("Retried or skipped attempts (a pass after a retry is a flaky test):\n" + "\n".join(f"- {r}" for r in retried))
    if error_lines and not failing:
        summary.append("First error: " + lines[error_lines[0]].strip()[:300])

    meta = dict(job=job, build=build, result=result, tests=tests)
    chunks = [ctx.chunk("\n".join(summary), title=label, location="build summary", source_type="build", kind="summary", **meta)]

    # Failure windows: each error with context, overlapping windows merged.
    windows: list[list[int]] = []
    for i in error_lines:
        start, end = max(0, i - 6), min(len(lines), i + 13)
        while end < len(lines) and _STACK.match(lines[end]) and end - i < 40:
            end += 1  # keep the stack trace together
        if windows and start <= windows[-1][1]:
            windows[-1][1] = max(windows[-1][1], end)
        else:
            windows.append([start, end])
    covered = set()
    for start, end in windows[:25]:
        stage = _stage_at(stages, start)
        for w in split_lines(lines[start:end], chunking.max_tokens, chunking.overlap_tokens, CODE_CHARS_PER_TOKEN, start + 1):
            covered.update(range(w.first_line, w.last_line + 1))
            chunks.append(
                ctx.chunk(
                    f"{head} · failure in stage {stage} · log lines {w.first_line}-{w.last_line}\n\n{w.text}",
                    title=label, location=f"{stage} · L{w.first_line}-L{w.last_line}",
                    source_type="build", kind="failure", stage=stage, **meta,
                )
            )

    # The remaining lines, stage by stage.
    for n, (name, start) in enumerate(stages):
        end = stages[n + 1][1] if n + 1 < len(stages) else len(lines)
        body = lines[start:end]
        if not body or all(start + k + 1 in covered for k in range(len(body))):
            continue
        for w in split_lines(body, chunking.max_tokens, chunking.overlap_tokens, CODE_CHARS_PER_TOKEN, start + 1):
            if all(k in covered for k in range(w.first_line, w.last_line + 1)):
                continue
            chunks.append(
                ctx.chunk(
                    f"{head} · stage {name} · log lines {w.first_line}-{w.last_line}\n\n{w.text}",
                    title=label, location=f"{name} · L{w.first_line}-L{w.last_line}",
                    source_type="build", kind="stage", stage=name, **meta,
                )
            )
    return chunks


def _playwright_groups(lines: list[str]) -> dict[str, list[str]]:
    """The end-of-run lists Playwright prints under '1 failed' and '1 flaky'."""
    groups: dict[str, list[str]] = {}
    current = ""
    for line in lines:
        header = _PLAYWRIGHT_GROUP.match(line)
        if header:
            current = header.group(1)
            groups.setdefault(current, [])
        elif current and _PLAYWRIGHT_TEST.match(line):
            groups[current].append(line.strip())
        elif line.strip():
            current = ""
    return {k: v for k, v in groups.items() if v}


def _stage_at(stages: list[tuple[str, int]], line: int) -> str:
    current = stages[0][0]
    for name, start in stages:
        if start <= line:
            current = name
    return current


def _test_counts(raw: str) -> dict[str, int]:
    runs = _SUREFIRE.findall(raw)
    if runs:
        total, failures, errors, skipped = (int(x) for x in runs[-1])
        return {"run": total, "failed": failures, "errors": errors, "skipped": skipped}
    counts = {}
    for number, kind in _PLAYWRIGHT_COUNT.findall(raw):
        counts[kind] = int(number)
    return counts


# --- Test reports -----------------------------------------------------------------


def _xml_report(ctx: LoadContext, job: str, build: str) -> list[Chunk]:
    try:
        root = ET.fromstring(read_text(ctx.path).encode("utf-8"))
    except ET.ParseError as error:
        raise Unsupported(f"XML could not be parsed: {error}") from None
    tests: list[dict] = []
    if root.tag == "testng-results":
        for method in root.iter("test-method"):
            if method.get("is-config") == "true":
                continue
            klass = next((c.get("name", "") for c in root.iter("class") if method in list(c)), "")
            exc = method.find("exception")
            tests.append({
                "name": method.get("name", ""),
                "class": klass,
                "status": {"PASS": "passed", "FAIL": "failed", "SKIP": "skipped"}.get(method.get("status", ""), method.get("status", "").lower()),
                "seconds": round(int(method.get("duration-ms", "0") or 0) / 1000, 2),
                "message": (exc.findtext("message") or "").strip() if exc is not None else "",
                "trace": (exc.findtext("full-stacktrace") or "").strip() if exc is not None else "",
            })
        kind = "TestNG"
    elif root.tag in ("testsuite", "testsuites"):
        # A report may name its build: <property name="jenkins.build" value="vwo-selenium-regression #142"/>
        for prop in root.iter("property"):
            if prop.get("name") in ("jenkins.build", "build", "BUILD_TAG"):
                named = _JOB_BUILD.match((prop.get("value") or "").strip())
                if named:
                    job, build = named.group("job").strip(), named.group("build")
        for case in root.iter("testcase"):
            problem = case.find("failure")
            if problem is None:
                problem = case.find("error")
            status = "passed"
            if problem is not None:
                status = "failed" if problem.tag == "failure" else "error"
            elif case.find("skipped") is not None:
                status = "skipped"
            if case.find("flakyFailure") is not None or case.find("rerunFailure") is not None:
                status = "flaky" if status == "passed" else status
            tests.append({
                "name": case.get("name", ""),
                "class": case.get("classname", ""),
                "status": status,
                "seconds": float(case.get("time", "0") or 0),
                "message": (problem.get("message") or "").strip() if problem is not None else "",
                "trace": (problem.text or "").strip() if problem is not None else "",
            })
        kind = "JUnit"
    else:
        raise Unsupported(f"<{root.tag}> is not a JUnit or TestNG report")
    return _report_chunks(ctx, job, build, kind, tests)


def _playwright_report(ctx: LoadContext, job: str, build: str) -> list[Chunk]:
    try:
        data = json.loads(read_text(ctx.path))
    except json.JSONDecodeError as error:
        raise Unsupported(f"JSON could not be parsed: {error}") from None
    if not isinstance(data, dict) or "suites" not in data:
        raise Unsupported("JSON is not a Playwright JSON report")
    tests: list[dict] = []

    def walk(suite: dict, path: list[str]) -> None:
        title = suite.get("title", "")
        here = path + ([title] if title and not title.endswith((".ts", ".js")) else [])
        for spec in suite.get("specs", []):
            for test in spec.get("tests", []):
                results = test.get("results", [])
                last = results[-1] if results else {}
                error = (last.get("error") or {}).get("message", "") or next(
                    ((r.get("error") or {}).get("message", "") for r in results if r.get("error")), ""
                )
                tests.append({
                    "name": " > ".join(here + [spec.get("title", "")]),
                    "class": f"{spec.get('file', '')}:{spec.get('line', '')} [{test.get('projectName', '')}]",
                    "status": {"expected": "passed", "unexpected": "failed"}.get(test.get("status", ""), test.get("status", "")),
                    "seconds": round(sum(r.get("duration", 0) for r in results) / 1000, 2),
                    "retries": max((r.get("retry", 0) for r in results), default=0),
                    "message": strip_ansi(error).strip(),
                    "trace": "",
                })
        for child in suite.get("suites", []):
            walk(child, here)

    for suite in data.get("suites", []):
        walk(suite, [])
    return _report_chunks(ctx, job, build, "Playwright", tests)


def _report_chunks(ctx: LoadContext, job: str, build: str, kind: str, tests: list[dict]) -> list[Chunk]:
    if not tests:
        return []
    label = _label(job, build)
    chunking = ctx.source.chunking
    counts: dict[str, int] = {}
    for t in tests:
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    head = f"{kind} test results · build {label} · {ctx.path.name}"
    totals = "Totals: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
    meta = dict(job=job, build=build, tests=counts, report=kind)

    # Every test with its status, for history and flaky-test questions.
    rows = [f"- {t['status'].upper()}: {t['class'] + ' > ' if t['class'] else ''}{t['name']} ({t['seconds']}s"
            + (f", {t['retries']} retries" if t.get("retries") else "") + ")"
            for t in sorted(tests, key=lambda t: (t["status"] == "passed", t["class"], t["name"]))]
    chunks = []
    budget = chunking.max_tokens - estimate_tokens(head + totals)
    for w in split_lines(rows, budget, 0):
        chunks.append(ctx.chunk(f"{head}\n{totals}\n\n{w.text}", title=label, location="test summary",
                                source_type="build", kind="report", **meta))

    for t in tests:
        if t["status"] in ("passed", "skipped"):
            continue
        trace = "\n".join(t["trace"].split("\n")[:25])
        body = "\n".join(x for x in (
            f"Test: {t['class'] + ' > ' if t['class'] else ''}{t['name']}",
            f"Status: {t['status']} · duration {t['seconds']}s" + (f" · {t['retries']} retries" if t.get("retries") else ""),
            t["message"] and f"Message: {t['message'][:1500]}",
            trace and f"Stack trace:\n{trace}",
        ) if x)
        text = mask_secrets(f"{head}\n{body}", inline=True)
        chunks.append(ctx.chunk(text[: int(chunking.max_tokens * CODE_CHARS_PER_TOKEN)], title=label,
                                location=f"{t['status']} · {t['name'][:60]}", source_type="build", kind="test",
                                test=t["name"], status=t["status"], **meta))
    return chunks
