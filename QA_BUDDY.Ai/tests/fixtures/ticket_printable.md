Back to previous view
[DEMO-7] Checkout shows two cart rows with parallel workers Created: 09/Apr/26  Updated: 10/Apr/26
Status:	To Do
Project:	Demo Project
Components:	Checkout, CI
Affects versions:	None
Fix versions:	None

Type:	Bug	Priority:	High
Reporter:	Alice	Assignee:	Unassigned
Resolution:	Unresolved	Votes:	0
Labels:	parallel, flaky

 Description 	 
The checkout test asserts one cart row but gets two when four workers share one user.

Comments
Comment by Bob [ 10/Apr/26 ]
Give each worker its own user.


Generated at Sat Apr 11 10:03:05 UTC 2026 by Alice using Jira 1001.0.0-SNAPSHOT.
