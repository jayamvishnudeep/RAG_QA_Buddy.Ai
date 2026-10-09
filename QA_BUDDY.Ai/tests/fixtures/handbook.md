# QA Handbook

Short intro to how the team works.

## Flaky tests

A test that fails and then passes on retry is flaky. Two flaky builds in seven days put the test in quarantine.

## Code review

Every automation pull request needs one reviewer from the QA team.

```java
// example
WaitHelpers.visibilityOfElement(driver, locator);
```
