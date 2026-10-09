# Sprint 15 QA planning

**Date:** 2026-04-10
**Attendees:** Pramod (QA Lead), Ananya Rao (SDET), Kiran Shah (QA Analyst), Meera Iyer (PM)

## Agenda
1. Regression health after the CI migration
2. Automation coverage
3. Coverage gaps against the VWO PRD
4. Flaky test policy

## 1. Regression health
- Build #143 is green after the NAT IP allowlist fix (QAB-101), but `testLoginPositiveVWO` needed one retry. TestNG counts the failed attempt as *skipped*, so a rising skip count is our early flaky signal.
- playwright-e2e #88 is unstable: checkout fails on all retries because parallel workers share one user's cart (QAB-103). The booking `create a booking` test was flaky once (passed on retry #1).

## 2. Automation coverage
- The repository holds **500 test cases**, of which **105 are automated (21%)**.
- Target for end of Q2: **40% automated**, prioritising the 110 Critical cases first.
- New Selenium tests must follow the improved POM (`improved_POM` package) with explicit waits from `WaitHelpers`. No new `waitJVM` calls.

## 3. Coverage gaps against the PRD
- Test cases today cover login, valid/invalid login, admin, admin-logged-in, support and A/B testing.
- The PRD also describes Insights (heatmaps, session recordings, funnels), Personalization, Program & Workflow Management and Integrations (Shopify, Salesforce, Segment, Snowflake). We are not sure these have any test cases.
- Kiran will run a gap analysis with QABuddy (PRD vs test case repository) and bring a list of missing scenarios to the next planning.

## 4. Flaky test policy
- A test is *flaky* when it fails and then passes on retry in the same build.
- Two flaky builds within 7 days: quarantine the test (Jira label `flaky`) and fix within the sprint.
- RetryAnalyzer stays at `maxRetryCount = 3`; retries are a safety net, not a fix.

## Decisions
- Prioritise automation of Critical test cases.
- Gap analysis against the PRD is a Sprint 15 deliverable.
- Per-worker test users for Playwright (fixes QAB-103 and future server-side state clashes).

## Action items
- Kiran Shah: PRD vs test case gap analysis using QABuddy (due 2026-04-17).
- Ananya Rao: worker-scoped user fixture in the Playwright framework (QAB-103).
- Pramod: publish the updated flaky test policy in the QA Process Handbook.
