Back to previous view
[QAB-103] Playwright checkout test sees 2 cart rows instead of 1 when running with 4 workers Created: 09/Apr/26  Updated: 10/Apr/26
Status:	To Do
Project:	QABuddy Sample
Components:	Playwright Framework

Type:	Bug	Priority:	High
Reporter:	Ananya Rao	Assignee:	Unassigned
Resolution:	Unresolved	Votes:	0
Labels:	playwright, parallel, test-isolation

 Description 	 
playwright-e2e build #88 failed: e2e-checkout.spec.ts "should complete checkout successfully" asserts cartPage.rowCount() toBe(1) at line 56 but receives 2. It failed on the first run and on both CI retries (retries: 2 in playwright.config.ts), so this is a real failure, not a flake.

The suite runs fullyParallel with 4 workers on CI. All workers log in as the same standard user, and the cart is stored server-side per user, so an item added by a parallel test appears in this test's cart.

Expected result: Each test starts with an empty cart.
Actual result: The cart contains an item added by another worker.

Comments
Comment by Pramod [ 10/Apr/26 ]
Two options: give each worker its own user via a worker-scoped fixture, or clear the cart in test.beforeEach. Prefer the per-worker user, it also removes contention on other server-side state.


Generated at Sat Apr 11 10:03:05 UTC 2026 by Ananya Rao using Jira 1001.0.0-SNAPSHOT.
