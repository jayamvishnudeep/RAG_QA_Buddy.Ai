Here is the important instruction I'm giving you: I want you to build a QA buddy.ai. I have multiple repositories, multiple folder structures, and multiple things that I have in my company that I want to connect all of them into the qabuddyai. I want you to give me a plan:

Which open-source embedding model should I use?
Which open-source vector database should I use?
How should the structure or the plan be?

We will have a chatbot that I will be deploying to one of the DigitalOcean machines, hosting, or any type of virtual private network. It will be working and will be used in my company.

I want one more thing: this should be every hour. There should also be an auto-ingestion. For example, if there is a new test case, new code in the repository, or anything, that should also happen, but that is phase two.

In phase one, I have all the folder structures and everything. I will put some of the documents, resources, and other things. Your task is to build an end-to-end system that I will also be able to fetch the data from.

 I am going to give you a rough diagram. Prepare a plan and let me know how you have thought about it and what you think about what should be the accurate chunk size, overlap, and the terminology and text I should use.

here is the details, which we have to use

# Building LIVE QABuddyAI, with advanced Hybrid RAG

Objective :  Multi-source Retrieval-Augmented Generation for QA engineers.

- Ask one question an get a reply
- get a cited answer grounded in your Selenium framework, your Playwright framework, your VWO test case repository, your PRDs, and your JIRA bug history.

**What are the main usage of this RAG ?**

- New user can get help from here.
- Knowledge Base for QA
- KB Brain with Code Repos
- One Stop resouece system.
- It is updated also ( 24x7) available, and it takes less token.
- Perform Test Failure analyse
- Find the Test case creation, Find the Test Plan,  Review, Identity the missing Testcase
- Identify the Testcases.
- Builds RTM, buG triage, RCA, Test Analyst.
- Flaky testcase, build data you can fed and retrieve.
- Best Coding, Your Framework level coding execerise, scripts and knowledge and doubt buster also.

1. GHCP -> TC - JIRA ID (MCP) -> 30-40% Test coverage
2. GHCP + RAG(LLM)+ JIRA ID -> 70-80% -> Test Coverage.

---

**Requirement Properly**

1. Selenium Repo - [github.com/PramodDutta/ATB13xSeleniumAdvanceFramework](https://github.com/PramodDutta/ATB13xSeleniumAdvanceFramework)
2. Playwright Repo. [github.com/PramodDutta/Advance-Playwright-Framework](https://github.com/PramodDutta/Advance-Playwright-Framework)
3. Test Cases - 5000 TC - CSV, Xlsx -> testdata.csv
4. JIRA Tickets
5. Company PDF, MD files.
6. FIGMA Design ( Phase 2)

   1. ER Diagram
   2. User Guides
   3. Wireframes
7. Metting Notes, Recording - Text
8. Lucid Chart -> Text ->
9. PRD, SRS, BRD, FRD all PDFs
10. Jenkins Logs and Results.

For all these 10 things, I want you to create a folder for them so that I can put everything here. First of all, you need to create a folder for 10 things. I will be putting the data there so that you can fetch it. Also, use the Jira MCB connection. I will be sharing with you my connection to fetch the Jira tickets. Also, I will give you a JQL. By using the JQL, you will be able to fetch all the Jiras. Also, we need to put them into RAG.
