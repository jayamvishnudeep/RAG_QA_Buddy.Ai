# 07_Jenkins_Logs

Jenkins build output for failure analysis and flaky-test questions. QABuddy reads:

- console logs, named `JOB/BUILD/console.log` or `JOB_BUILD.log` (the path gives the job and build number);
- JUnit and TestNG XML reports;
- Playwright JSON reports.

No logs are included yet. Files whose names start with `_README` are not indexed.
