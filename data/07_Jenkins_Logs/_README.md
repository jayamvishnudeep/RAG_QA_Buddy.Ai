# 07_Jenkins_Logs

Jenkins build output for failure analysis and flaky-test questions. QABuddy reads:

- console logs, named `JOB/BUILD/console.log` or `JOB_BUILD.log` (the path gives the job and build number);
- JUnit and TestNG XML reports (a `jenkins.build` property such as `vwo-selenium-regression #142` names the build);
- Playwright JSON reports.

The sample logs here cover Selenium builds #142 (login blocked by the IP allowlist) and #143 (a flaky retry),
and Playwright build #88 (a failing checkout and a flaky booking test). Files whose names start with
`_README` are not indexed.
