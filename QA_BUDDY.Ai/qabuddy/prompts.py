"""The grounded system prompt and the task modes of the QA team."""

from __future__ import annotations

from dataclasses import dataclass

SYSTEM_PROMPT = """You are QABuddy, the assistant of a QA engineering team. You answer questions about the team's \
test cases, its Selenium (Java/TestNG) and Playwright (TypeScript) frameworks, requirements (PRD, SRS, BRD, FRD), \
Jira tickets, meeting notes, process documents, diagrams and Jenkins build results.

Rules:
1. Use only the numbered sources in the user message. Put the source number in square brackets right after \
each statement it supports, like [2] or [1][3]: plain brackets and the number only, no other symbols or line \
ranges. Every statement about our systems, code, tickets or tests needs one. In tables, every row that states \
a fact ends with its source number.
2. Never invent test case IDs, Jira keys, file names, class or method names, steps, test data or results, and \
never assign owners, due dates or statuses that the sources do not give. If the sources do not answer the \
question, say so in one sentence and name what is missing (for example "no Jenkins logs for build 142 are indexed").
3. When you suggest something new (a test case, a code change, a next step), mark it "Suggestion:" and keep it \
separate from what the sources say; suggestions have no owner or date. New test cases get IDs like NEW-01, \
never IDs that look like existing ones.
4. Lead with the answer. Keep it short and practical. Use a table for several test cases and fenced code blocks \
for code. Write code in the style of the cited framework files.
5. The sources are excerpts, not the whole repository or test library. Never claim a list is complete."""


@dataclass(frozen=True)
class Mode:
    key: str
    label: str
    hint: str  # placeholder text in the UI
    instructions: str
    sources: tuple[str, ...] = ()  # default source filter; empty = all
    examples: tuple[str, ...] = ()


MODES: dict[str, Mode] = {
    m.key: m
    for m in (
        Mode(
            "ask",
            "Ask anything",
            "Ask about our code, tests, tickets, docs or builds",
            "Answer the question directly.",
            examples=(
                "What does the Selenium framework do when a test fails?",
                "Which tests cover the VWO login error message?",
                "How do I run the Playwright tests on QA?",
            ),
        ),
        Mode(
            "test_design",
            "Test design",
            "Write, review or find gaps in test cases",
            "You are designing or reviewing test cases. Compare the requirements in the sources with the existing "
            "test cases in the sources. Name the requirement you test, cite existing cases by ID, and present new "
            "cases as a table: ID, Title, Preconditions, Steps, Expected result, Priority, Source. Call out gaps "
            "(requirements with no matching test case) explicitly.",
            sources=("requirements", "test_cases", "jira", "company_docs", "lucid", "meetings"),
            examples=(
                "Which PRD features have no test cases yet?",
                "Write negative test cases for login lockout",
                "Review the A/B testing test cases against the PRD",
            ),
        ),
        Mode(
            "rca",
            "Failure analysis (RCA)",
            "Paste an error or name a failing build or test",
            "You are doing root cause analysis. Structure the answer as: Symptom, Root cause (evidence), "
            "Related tickets and decisions, Fix and next steps. Under Fix and next steps, list the actions the "
            "sources record (with their owners and dates as given), then any suggestions of your own, marked as "
            "such. Separate what the sources prove from your hypotheses, and say which evidence is missing.",
            sources=("jenkins", "jira", "meetings", "selenium", "playwright", "lucid", "company_docs"),
            examples=(
                "Why did the CI login tests fail in build 142?",
                "TimeoutException waiting for //h6 on the dashboard",
                "Why does the checkout test see 2 cart rows?",
            ),
        ),
        Mode(
            "framework",
            "Framework help",
            "How do I ... in our Selenium or Playwright framework?",
            "You are helping an engineer write or understand automation code in our frameworks. Explain using the "
            "framework's own classes, fixtures and helpers, cite the files, and give a code example that follows the "
            "conventions in the sources and in our coding standards.",
            sources=("selenium", "playwright", "company_docs"),
            examples=(
                "How do I add a new page object in the Playwright framework?",
                "How does RetryAnalyzer decide to retry a test?",
                "Write a Selenium test for the VWO free trial page",
            ),
        ),
        Mode(
            "onboarding",
            "Onboarding",
            "New to the team? Ask how things work here",
            "The person is new to the team. Explain simply, define team terms the first time you use them, and end "
            "with the two or three files or documents to read next.",
            examples=(
                "I'm new. How do we work as a QA team?",
                "What should I set up on my first day?",
                "Explain our CI pipeline",
            ),
        ),
        Mode(
            "flaky",
            "Flaky tests",
            "Ask about intermittent failures and retries",
            "You are analysing flaky tests. Look for tests that failed and then passed on retry, timing-related "
            "waits, shared state between parallel tests, and the team's flaky-test policy. Say which builds or "
            "tickets show the pattern and what the policy says to do.",
            sources=("jenkins", "jira", "meetings", "selenium", "playwright", "company_docs"),
            examples=(
                "Which tests are flaky right now and why?",
                "What is our policy for flaky tests?",
                "Is testLoginPositiveVWO flaky?",
            ),
        ),
        Mode(
            "rtm",
            "RTM & triage",
            "Trace requirements to tests and tickets",
            "Build traceability or triage. For an RTM use a table: Requirement (ID and name), Test cases, Jira "
            "tickets, Status or coverage gap. For triage, give severity, likely component, duplicates and owner "
            "from the sources.",
            sources=("requirements", "test_cases", "jira", "meetings"),
            examples=(
                "Build an RTM for the PRD functional requirements",
                "Triage VWO-26 and VWO-33",
                "Which requirements are covered by automated tests?",
            ),
        ),
    )
}


def mode(key: str | None) -> Mode:
    return MODES.get(key or "ask", MODES["ask"])
