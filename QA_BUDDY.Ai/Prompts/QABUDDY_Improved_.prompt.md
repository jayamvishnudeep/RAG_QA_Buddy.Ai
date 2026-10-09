# QABuddy.ai — Build Prompt (Advanced Hybrid RAG for QA Engineers)

## 1. Context & Role

You are acting as a senior AI engineer. Design and build **QABuddy.ai** — a self-hosted, multi-source **Hybrid RAG** (Retrieval-Augmented Generation) system for the QA engineers at my company.

The end product: a QA engineer asks one question and gets a single **cited answer**, grounded in our Selenium framework, Playwright framework, VWO test case repository, PRDs, and JIRA bug history.

## 2. Objective

Build an end-to-end system that:

1. Ingests code and documents from a defined folder structure (Phase 1)
2. Chunks, embeds, and indexes them in an **open-source vector database** with hybrid (keyword + semantic) retrieval
3. Serves grounded answers **with citations** through a chatbot
4. Runs 24x7 on a DigitalOcean droplet (or similar VPS / self-hosted server) for internal company use
5. Keeps token usage low by retrieving only the relevant context

## 3. Use Cases to Support

- **Onboarding:** new team members can self-serve answers
- **QA knowledge base:** a "KB brain" connected to our code repos — a one-stop resource system
- **Test failure analysis** and root cause analysis (RCA)
- **Test design:** test case creation, test plan drafting, test case review, and identifying missing test cases (gaps)
- **Process artifacts:** RTM building, bug triage, and test analyst workflows
- **Flaky tests:** feed in build/run data and retrieve flaky test history later
- **Framework coding help:** best practices, scripts, coding exercises, and doubt-busting at our framework level

**Target impact on test coverage:**

| Setup | Expected test coverage |
|---|---|
| GitHub Copilot + JIRA ID (MCP) | ~30–40% |
| GitHub Copilot + RAG (LLM) + JIRA ID | ~70–80% |

## 4. Data Sources (10)

**Your first task: create one folder per source below.** I will place the Phase 1 data into these folders so your pipeline can fetch it.

| # | Source | Format / location | Phase |
|---|---|---|---|
| 1 | Selenium framework repo | https://github.com/PramodDutta/ATB13xSeleniumAdvanceFramework | 1 |
| 2 | Playwright framework repo | https://github.com/PramodDutta/Advance-Playwright-Framework | 1 |
| 3 | Test cases (~5,000) | CSV / XLSX (e.g., `testdata.csv`) | 1 |
| 4 | JIRA tickets | Live via **JIRA MCP connection + JQL** (I will share both) | 1 |
| 5 | Company docs | PDF, MD | 1 |
| 6 | Figma designs — ER diagrams, user guides, wireframes | Figma exports | **2** |
| 7 | Meeting notes & recordings | Text transcripts | 1 |
| 8 | Lucid charts | Exported to text | 1 |
| 9 | PRD / SRS / BRD / FRD | PDF | 1 |
| 10 | Jenkins logs & results | Log / text files | 1 |

## 5. Phase 1 — Build Now

1. Create the folder structure for all 10 sources
2. Build the ingestion pipeline: parse → clean → chunk → embed → index, with source-appropriate handling (code vs. spreadsheet rows vs. prose vs. logs)
3. Connect to JIRA via MCP and ingest all tickets returned by the JQL I provide
4. Implement hybrid retrieval with citations — every answer must reference its source file / ticket
5. Build the chatbot layer, deployable to my DigitalOcean / VPS machine

## 6. Phase 2 — Plan Only (do not build yet)

- **Hourly auto-ingestion:** detect new test cases, new repo commits, or new documents, and re-index automatically every hour
- Figma design ingestion (ER diagrams, user guides, wireframes)

## 7. Decisions I Need From You (with justification)

1. Which **open-source embedding model** should I use?
2. Which **open-source vector database** should I use?
3. What is the accurate **chunk size and overlap** — per source type (code, test case rows, PDFs, transcripts, logs)?
4. What **preprocessing / text normalization** should be applied (terminology handling, metadata, cleanup)?
5. What should the overall **architecture, structure, and plan** be?

## 8. Constraints

- Embedding model and vector database must be **open source**
- **Self-hosted** on a DigitalOcean droplet / VPS, used internally within my company
- Available 24x7 and token-efficient

## 9. How to Respond

I will also share a rough architecture diagram. **Before writing any code:**

1. Present your full plan and architecture, and explain how you thought about it
2. Justify your choices for the embedding model, vector DB, chunk size, and overlap
3. Show the proposed folder structure for the 10 sources

Wait for my approval, then implement Phase 1 end-to-end.