# QA Process Handbook

Sample company document generated for testing QABuddy. Owner: Pramod (QA Lead). Version 3.2, April 2026.

## 1. Environments

| Environment | URL | Used for |
|---|---|---|
| QA | https://app.vwo.com (test accounts only) | Nightly Selenium regression, manual testing |
| Staging | https://stage.thetestingacademy.com | Playwright e2e before release |
| Production | https://app.thetestingacademy.com | Smoke tests after release only |

Test accounts live in `data.properties` (Selenium) and `src/config/credentials.ts` (Playwright). Never commit real customer credentials. CI agents must be on the test account IP allowlist (see QAB-101).

## 2. Test case standards

### 2.1 Test case IDs
IDs follow `MODULE-NNN`, for example `LOGIN-002`, `ADMIN-014`, `SUPPORT-051`, `ABTEST-007`. The module prefix must match one of: LOGIN, VALID, INVALID, ADMIN, ADMINLOGGED, SUPPORT, ABTEST. New modules need QA Lead approval.

### 2.2 Required fields
Every test case has: Scenario TID, TestCase Description (starting with "Verify"), PreCondition, TestSteps, Expected Result, Priority and Is Automated. Steps are numbered and separated by " | " in the CSV export.

### 2.3 Priority definitions
- **Critical**: blocks login, payment, data security or a release. Must be automated first.
- **High**: core feature broken with no workaround.
- **Medium**: feature works with a workaround, or a cosmetic issue in a core flow.

## 3. Bug triage

### 3.1 Severity vs priority
Severity is the technical impact; priority is how soon we fix it. Triage sets both.

| Severity | Meaning | Example |
|---|---|---|
| S1 | System or security failure, no workaround | Users cannot log in with valid credentials |
| S2 | Major feature broken, workaround exists | A/B test report does not load |
| S3 | Minor feature or UI issue | Misaligned button on the support page |
| S4 | Cosmetic | Typo in a tooltip |

### 3.2 Triage rules
1. Search Jira and QABuddy for duplicates before filing. Mark duplicates and link the original (VWO-33 is a duplicate of VWO-26).
2. Every bug links the failing test case ID and, for CI failures, the Jenkins build number.
3. A login failure for valid credentials is S1 until proven to be environment-specific.
4. Environment issues (IP allowlists, expired test accounts, agent problems) are filed in the QAB project, not as product bugs.

## 4. Flaky test policy
A test is **flaky** when it fails and then passes on retry within the same build. TestNG reports the failed attempt as SKIPPED, so watch the skip count.

- Two flaky builds within 7 days: quarantine the test with the Jira label `flaky` and fix it within the sprint.
- `RetryAnalyzer` keeps `maxRetryCount = 3`. Never raise it to hide a failure.
- Playwright uses `retries: 2` on CI only. A test that fails all retries is a real failure, not a flake.
- Common root causes: hard sleeps (`Thread.sleep`, `waitJVM`, `page.waitForTimeout`), shared test users across parallel workers, and order-dependent data.

## 5. Definition of Done for a test case
- Reviewed by a second QA engineer.
- Linked to its requirement (PRD section) in the RTM.
- Automated cases reference the automation method name in the test case comments.

## 6. Requirements traceability (RTM)
The RTM maps PRD/SRS requirement → test case IDs → automation status → open bugs. Every PRD section in "4. Core Features & Capabilities" needs at least one Critical or High test case before a release.

## 7. Release sign-off
- No open S1 bugs; S2 bugs need PM sign-off.
- Regression pass rate at least 98% with zero unexplained failures.
- All quarantined flaky tests have an owner and a due date.
