# QABuddy data

The knowledge sources [QABuddy](../README.md) indexes, one folder each. Which folder feeds which source, and how
each is chunked, is set in [`../QA_BUDDY.Ai/config/sources.yaml`](../QA_BUDDY.Ai/config/sources.yaml).

| Folder | Source | What is in it |
|---|---|---|
| [00_TestCases](00_TestCases/) | Test cases | `VWO_500_Test_Cases.csv` (500) and `Wingify_Login_100_Jira_Test_Cases.csv` (100) |
| [01_Jira_Ticket](01_Jira_Ticket/) | Jira tickets | Two real Jira exports (VWO-26 in `Bug_VWO_26.md`, VWO-33 in `Bug_VWO_32.md`) and three sample tickets, QAB-101 to QAB-103 |
| [02_Company_Documents](02_Company_Documents/) | Company docs | Automation coding standards, a QA onboarding guide (PDF) and a QA process handbook |
| [03_Meeting_Notes](03_Meeting_Notes/) | Meeting notes | A triage meeting, a sprint planning and a daily standup, as `.txt`, `.md` and `.vtt` |
| [04_Lucid_Charts](04_Lucid_Charts/) | Lucid charts | An A/B test lifecycle, a CI test pipeline and a login flow, as JSON, text and CSV exports |
| [05_PRD_SRS_BRD_FRDs](05_PRD_SRS_BRD_FRDs/) | PRD / SRS / BRD / FRD | The VWO product requirements document (PDF) |
| [06_Figma_Desings](06_Figma_Desings/) | Figma designs | Empty for now: Phase 2, not indexed yet |
| [07_Jenkins_Logs](07_Jenkins_Logs/) | Jenkins logs | Console logs of Selenium builds #142 and #143 and Playwright build #88, plus the JUnit report of #142 |
| [08_Source_Codes](08_Source_Codes/) | Selenium and Playwright frameworks | Nothing committed: clone your framework repositories here (see its `_README.md`) |

The test cases, the PRD and the two VWO exports come from a training project. The other tickets, documents,
meeting notes, charts and logs are sample data written for QABuddy.

Files whose names start with `_README` are notes about a folder and are never indexed. Do not put a plain
`README.md` inside a source folder, because it would be indexed as team knowledge.
