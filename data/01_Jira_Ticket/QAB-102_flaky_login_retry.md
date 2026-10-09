Back to previous view
[QAB-102] Flaky: testLoginPositiveVWO passes only on retry, dashboard renders after the hard 5s sleep Created: 08/Apr/26  Updated: 10/Apr/26
Status:	In Progress
Project:	QABuddy Sample
Components:	Selenium Framework

Type:	Bug	Priority:	Medium
Reporter:	Ananya Rao	Assignee:	Pramod
Resolution:	Unresolved	Votes:	0
Labels:	flaky, selenium, wait

 Description 	 
In build #143 testLoginPositiveVWO (TestVWOLogin_05_TakeScreen_Retry_Prop_Improved_POM) failed its first attempt with TimeoutException waiting for //h6 and passed on retry 1 via RetryAnalyzer. TestNG reports the failed attempt as SKIPPED, so the build is green and the flake is easy to miss.

LoginPage.loginToVWOLoginValidCreds() waits with WaitHelpers.waitJVM(5000), a Thread.sleep, instead of waiting for the dashboard. When the dashboard takes longer than the sleep plus the 20 s explicit wait in DashBoardPage.loggedInUserName(), the attempt fails.

Proposed fix: replace waitJVM(5000) with an explicit wait on the dashboard header, and stop relying on RetryAnalyzer (maxRetryCount = 3) to hide timing issues.

Comments
Comment by Pramod [ 09/Apr/26 ]
Agreed. Per the flaky test policy this test is quarantined until the wait is fixed. Do not raise maxRetryCount.
Comment by Ananya Rao [ 10/Apr/26 ]
Draft PR replaces the sleep with WaitHelpers.visibilityOfElement on the dashboard locator. 20 consecutive green runs locally.


Generated at Sat Apr 11 10:02:40 UTC 2026 by Ananya Rao using Jira 1001.0.0-SNAPSHOT.
