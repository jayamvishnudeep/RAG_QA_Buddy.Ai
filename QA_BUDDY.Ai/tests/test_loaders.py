import json

from qabuddy.loaders import code, diagrams, documents, jira, logs, tabular, transcripts
from qabuddy.loaders.base import RepoInfo


def test_java_is_chunked_by_declaration_with_scope_and_masking(make_ctx):
    chunks = code.load(make_ctx("LoginPage.java", "code", max_tokens=120, min_tokens=20))
    assert len(chunks) >= 2
    text = "\n".join(c.text for c in chunks)
    assert "abcd1234efgh5678" not in text  # quoted secret literal masked
    login = next(c for c in chunks if "public void login" in c.text)
    assert "class LoginPage" in login.text.split("\n\n", 1)[0]  # header names the enclosing class
    assert "login()" in login.meta["symbols"]
    assert "/** Logs in" in login.text  # doc comment stays with its method
    assert login.location.startswith("L")


def test_typescript_tests_are_found_by_name(make_ctx):
    chunks = code.load(make_ctx("checkout.spec.ts", "code", max_tokens=80, min_tokens=10))
    symbols = [s for c in chunks for s in c.meta.get("symbols", [])]
    assert "test('completes an order')" in symbols
    assert any("test.describe('Checkout')" in c.meta.get("scope", "") or "test.describe('Checkout')" in c.meta.get("symbols", []) for c in chunks)


def test_github_links_point_at_lines():
    repo = RepoInfo("https://github.com/acme/framework", "abc123")
    assert repo.file_url("src/a b.java", 3, 9) == "https://github.com/acme/framework/blob/abc123/src/a%20b.java#L3-L9"


def test_test_case_rows_drop_duplicated_and_composite_columns(make_ctx):
    chunks = tabular.load(make_ctx("cases.csv", "tabular", max_tokens=512))
    assert [c.title for c in chunks] == ["DEMO-TC-001", "DEMO-TC-002"]
    first = chunks[0].text
    assert first.startswith("Test case DEMO-TC-001: Sign in with valid credentials")
    assert first.count("The dashboard opens and shows the user name.") == 1  # duplicate column and composite Description dropped
    assert "1. Open the login page.\n2. Enter valid credentials." in first  # " | " step separators become lines
    assert chunks[0].meta["priority"] == "High"


def test_test_data_sheet_is_one_table_with_passwords_masked(tmp_path, make_ctx):
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.append(["email", "password"])
    for n in range(5):
        sheet.append([f"user{n}@example.com", f"Secr3t{n}xyz"])
    path = tmp_path / "TestData.xlsx"
    book.save(path)
    ctx = make_ctx("cases.csv", "code")
    ctx.path = path
    chunks = code.load(ctx)
    assert len(chunks) == 1
    text = chunks[0].text
    assert "user3@example.com | ***" in text and "Secr3t" not in text
    assert chunks[0].source_type == "test_data" and chunks[0].location == "rows 1-5"


def test_markdown_is_chunked_by_section(make_ctx):
    chunks = documents.load(make_ctx("handbook.md", "documents", max_tokens=60, overlap=0, min_tokens=0))
    assert len(chunks) >= 2
    assert any("Flaky tests" in c.location for c in chunks)  # merged small sections are named in the citation
    flaky = next(c for c in chunks if "quarantine" in c.text)
    assert flaky.text.startswith("QA Handbook")  # every chunk carries the document title


def test_vtt_merges_consecutive_cues_of_one_speaker(make_ctx):
    chunks = transcripts.load(make_ctx("standup.vtt", "transcripts", max_tokens=450))
    assert len(chunks) == 1
    text = chunks[0].text
    assert "Alice: The login suite is green again. One test still needed a retry." in text
    assert "Bob: I will look" in text
    assert chunks[0].location == "00:00:01-00:00:06"


def test_lucid_csv_becomes_an_outline(make_ctx):
    chunks = diagrams.load(make_ctx("flow.csv", "diagrams"))
    assert len(chunks) == 1
    assert "Cart empty? -> Show empty state (Yes)" in chunks[0].text
    assert chunks[0].location == "Checkout Flow"


def test_console_log_summary_and_failure_window(make_ctx):
    chunks = logs.load(make_ctx("demo-regression_7.txt", "logs", max_tokens=450, overlap=45))
    summary = chunks[0]
    assert summary.meta["kind"] == "summary"
    assert summary.meta["job"] == "demo-regression" and summary.meta["build"] == "7"
    assert "result FAILURE" in summary.text
    assert "Regression (errors)" in summary.text
    assert "1a2b3c4d5e6f" in summary.text
    failure = next(c for c in chunks if c.meta["kind"] == "failure")
    assert "TimeoutException" in failure.text and "WaitHelpers.java:31" in failure.text
    all_text = "\n".join(c.text for c in chunks)
    assert "Downloading from" not in all_text  # noise removed
    assert "hunter2secret" not in all_text  # secrets masked
    assert "repeated 2 more times" in all_text  # duplicate lines collapsed


def test_testng_report(make_ctx):
    chunks = logs.load(make_ctx("testng-results.xml", "logs"))
    report = chunks[0]
    assert "1 failed" in report.text and "1 passed" in report.text and "1 skipped" in report.text
    assert "setUp" not in report.text  # configuration methods are not tests
    failed = [c for c in chunks if c.meta.get("kind") == "test"]
    assert len(failed) == 1 and "testLoginPositive" in failed[0].text


def test_playwright_json_report_marks_flaky(make_ctx):
    chunks = logs.load(make_ctx("playwright-results.json", "logs"))
    tests = {c.meta["test"]: c.meta["status"] for c in chunks if c.meta.get("kind") == "test"}
    assert tests == {"Checkout > completes an order": "failed", "Checkout > shows the cart badge": "flaky"}
    assert "Received: 2" in next(c.text for c in chunks if c.meta.get("status") == "failed")


def test_jira_printable_export(make_ctx):
    chunks = jira.load(make_ctx("ticket_printable.md", "jira"))
    assert len(chunks) == 1
    c = chunks[0]
    assert c.meta["key"] == "DEMO-7" and c.meta["status"] == "To Do" and c.meta["priority"] == "High"
    assert c.meta["components"] == ["Checkout", "CI"] and c.meta["created"] == "2026-04-09"
    assert "Description:\nThe checkout test asserts one cart row" in c.text
    assert "2026-04-10 · Bob: Give each worker its own user." in c.text
    assert "Generated at" not in c.text


def test_jira_rest_json_with_rich_text(tmp_path, make_ctx, settings):
    issue = {
        "key": "DEMO-9",
        "fields": {
            "summary": "Login button does nothing",
            "issuetype": {"name": "Bug"},
            "status": {"name": "In Progress"},
            "priority": {"name": "High"},
            "labels": ["login"],
            "components": [{"name": "Auth"}],
            "created": "2026-05-01T10:00:00.000+0000",
            "description": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Click Sign in, nothing happens."}]}]},
            "comment": {"comments": [{"author": {"displayName": "Alice"}, "created": "2026-05-02T09:00:00.000+0000", "body": "Repro on Safari."}]},
        },
    }
    data_file = tmp_path / "issues.json"
    data_file.write_text(json.dumps({"issues": [issue]}), encoding="utf-8")
    ctx = make_ctx("ticket_printable.md", "jira")
    ctx.path = data_file
    chunks = jira.load(ctx)
    assert "Click Sign in, nothing happens." in chunks[0].text
    assert "2026-05-02 · Alice: Repro on Safari." in chunks[0].text
    assert chunks[0].meta["components"] == ["Auth"]


def test_jira_csv_export_with_repeated_columns():
    text = 'Summary,Issue key,Issue Type,Status,Labels,Labels,Comment\n"Cart shows 2 rows",DEMO-3,Bug,Open,ui,flaky,"09/Apr/26 10:15 AM;712020:abc;Seen on CI"\n'
    issues = jira.issues_from_csv(text)
    normalized = jira.normalize_issue(issues[0])
    assert normalized["key"] == "DEMO-3" and normalized["labels"] == ["ui", "flaky"]
    assert normalized["comments"][0]["body"] == "Seen on CI"


def test_timestamped_playwright_console_lists_failed_and_flaky_tests(make_ctx):
    chunks = logs.load(make_ctx("playwright-e2e_9.txt", "logs"))
    summary = chunks[0].text
    assert summary.startswith("Jenkins build playwright-e2e #9 · result UNSTABLE · ran 2026-04-09 11:03:00-11:03:50 UTC")
    assert "agent ci-agent-01" in summary
    assert "Tests: 1 failed, 1 flaky, 1 passed" in summary
    assert "Flaky tests (Playwright):\n- [chromium] › tests/booking.spec.ts:22:5 › create a booking" in summary
    assert "Failed tests (Playwright):\n- [chromium] › tests/checkout.spec.ts:30:5 › completes an order" in summary
    assert "[2026-04-09T" not in "\n".join(c.text for c in chunks)  # timestamp prefixes stripped


def test_junit_report_takes_the_build_from_its_properties(tmp_path, make_ctx):
    report = tmp_path / "TEST-TestSuite.xml"
    report.write_text(
        '<testsuite name="TestSuite" tests="1" failures="0"><properties>'
        '<property name="jenkins.build" value="vwo-selenium-regression #142"/></properties>'
        '<testcase name="t1" classname="C" time="1.0"/></testsuite>',
        encoding="utf-8",
    )
    ctx = make_ctx("testng-results.xml", "logs")
    ctx.path, ctx.rel_path = report, report.name
    chunks = logs.load(ctx)
    assert chunks[0].meta["job"] == "vwo-selenium-regression" and chunks[0].meta["build"] == "142"
