Back to previous view
[QAB-101] CI login tests fail with "IP address or location did not match" after Jenkins agent migration Created: 05/Apr/26  Updated: 08/Apr/26
Status:	Done
Project:	QABuddy Sample
Components:	CI, Login
Affects versions:	None
Fix versions:	None

Type:	Bug	Priority:	High
Reporter:	Ananya Rao	Assignee:	Rahul Verma
Resolution:	Fixed	Votes:	0
Labels:	ci, login, environment
Remaining Estimate:	Not Specified

 Description 	 
Since build #142 of vwo-selenium-regression, every positive login test fails on CI while passing locally.

Steps to reproduce:
1. Run testng_vwo_prop_improved_pom_part3.xml on any agent in the new GCP pool (ci-agent-gcp-01..04).
2. testLoginPositiveVWO submits valid credentials from data.properties.
3. The dashboard user name (//h6) never appears and WaitHelpers.visibilityOfElement times out after 20 s.

Expected result: Valid credentials log in and the dashboard shows the user name.
Actual result: VWO shows "Your email, password, IP address or location did not match" and stays on the login page. Screenshot attached by ScreenshotListener.

Suspected cause: VWO account security rejects logins from unrecognised IP ranges. The new agent pool egresses through a different NAT IP than the old on-prem agents.
Related: VWO-26, VWO-33 (same symptom reported by users).

Comments
Comment by Rahul Verma [ 07/Apr/26 ]
Confirmed. Old agents egressed via 203.0.113.24; the GCP pool uses NAT 35.200.14.7, which is not on the VWO test account IP allowlist.
Comment by Rahul Verma [ 08/Apr/26 ]
Added 35.200.14.7 to the allowlist. Build #143 is green for login; one retry was needed on testLoginPositiveVWO (tracked separately in QAB-102).
Comment by Ananya Rao [ 08/Apr/26 ]
Verified on #143. Closing. Added a pre-flight "login health check" stage proposal to the pipeline backlog so an IP block fails fast with a clear message.


Generated at Sat Apr 11 10:02:13 UTC 2026 by Ananya Rao using Jira 1001.0.0-SNAPSHOT.
