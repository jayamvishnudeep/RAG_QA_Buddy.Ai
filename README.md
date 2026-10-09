# QABuddy.ai: hybrid RAG for QA teams

Ask one question and get one cited answer, grounded in a team's Selenium and Playwright frameworks, test
cases, requirements, Jira tickets, meeting notes, diagrams and build logs.

**Live demo:** https://qabuddy-vishnudeep-jayam.vercel.app

![QABuddy answering "Why did the CI login tests fail in build 142?" with a cited root cause analysis: symptom, evidence, related tickets, and the recorded actions with owners and dates](QA_BUDDY.Ai/screenshots/04-rca-build-142.png)

| | |
|---|---|
| Embeddings | Qwen3-Embedding-0.6B (open source), served by Ollama |
| Vector database | Qdrant (open source): dense and BM25 sparse vectors, fused with Reciprocal Rank Fusion |
| Answers | Groq `openai/gpt-oss-120b` (open weights), or any OpenAI-compatible model |
| App | FastAPI and a plain HTML/JS chat UI with seven QA modes |
| Retrieval quality | 100% recall@5 on a 30-question regression set (semantic alone 87%, keyword alone 87%) |

## What's here

| Folder | What it is |
|---|---|
| [QA_BUDDY.Ai/](QA_BUDDY.Ai/) | The application: ingestion, hybrid retrieval, API, web UI, tests, Docker and Vercel deployment. Start with its [README](QA_BUDDY.Ai/README.md) |
| [QA_BUDDY.Ai/plan.md](QA_BUDDY.Ai/plan.md) | The design decisions: embedding model, vector database, chunk sizes, preprocessing, architecture, Phase 2 |
| [QA_BUDDY.Ai/screenshots/](QA_BUDDY.Ai/screenshots/) | One answered question per mode |
| [QA_BUDDY.Ai/Prompts/](QA_BUDDY.Ai/Prompts/) | The build briefs the project was built from |
| [data/](data/) | The knowledge sources, one folder each, with sample data |

The test cases, PRD and two Jira exports (VWO-26, VWO-33) come from a training project. The other tickets,
company documents, meeting notes and Lucid charts are sample data written for this project.

## Quick start

The two framework repositories are cloned, not copied:

```powershell
git clone https://github.com/PramodDutta/ATB13xSeleniumAdvanceFramework data/08_Source_Codes/ATB13xSeleniumAdvanceFramework
git clone https://github.com/PramodDutta/AdvancePlaywrightFramework1x data/08_Source_Codes/AdvancePlaywrightFramework1x
cd QA_BUDDY.Ai
```

Then follow [QA_BUDDY.Ai/README.md](QA_BUDDY.Ai/README.md#quick-start-windows-local).
