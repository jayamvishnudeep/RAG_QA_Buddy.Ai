# Automation Coding Standards

Sample company document generated for testing QABuddy. Applies to the Selenium (Java) and Playwright (TypeScript) frameworks.

## 1. Selenium framework (ATB13xSeleniumAdvanceFramework)

### 1.1 Structure
- Page classes live in `src/main/java/com/thetestingacademy/pages`. New VWO pages go in the `improved_POM` package.
- Tests live in `src/test/java/com/thetestingacademy/tests` and extend `CommonToAllTest`.
- Page classes extend `CommonToAllPage` and use its `enterInput`, `clickElement` and `getText` helpers.
- Get the driver from `DriverManager.getDriver()`. Never create a `ChromeDriver` inside a test.

### 1.2 Locators
- Declare locators as `private By` fields at the top of the page class, for example `private By username = By.id("login-username");`.
- Prefer `By.id` and stable `data-qa` attributes over long XPaths. `By.xpath("//h6")` is too generic for new code.

### 1.3 Waits
- Use the explicit waits in `WaitHelpers`: `visibilityOfElement`, `presenceOfElement`, `checkVisibility`.
- `WaitHelpers.waitJVM` wraps `Thread.sleep`. It is legacy: do not use it in new code, and replace it when you touch a method that calls it.
- Do not mix implicit and explicit waits.

### 1.4 Test data and config
- Read configuration with `PropertiesReader.readKey("key")` from `data.properties`.
- Data-driven tests read Excel through `UtilExcel` with a TestNG `@DataProvider`.

### 1.5 Retries, listeners and reporting
- Attach `RetryAnalyzer` only to suites that need it (see `testng_vwo_retry_prop_improved_pom_part4.xml`). The retry ceiling is 3.
- `ScreenshotListener` captures a screenshot on failure; keep it enabled on CI.
- Every test has Allure `@Owner` and `@Description` annotations.

## 2. Playwright framework (AdvancePlaywrightFramework1x)

### 2.1 Structure
- Page objects live in `src/pages`, extend `BasePage`, and are exported from `src/pages/index.ts`.
- Tests import `test` and `expect` from `@fixtures/test-base` (UI) or `@fixtures/booker.fixture` (API), never directly from `@playwright/test`.
- Wrap meaningful steps in `visualStep(page, 'title', async () => { ... })` so the report gets a screenshot per step.

### 2.2 Locators
- Use `data-test` attributes: `page.locator('[data-test="checkout"]')`, or role-based locators such as `getByRole('button', { name: 'Login' })`.
- No CSS chains that depend on layout.

### 2.3 Waits and assertions
- Rely on web-first assertions (`await expect(locator).toContainText(...)`). Never use `page.waitForTimeout`.
- Default timeouts come from `playwright.config.ts`: test 60 s, expect 10 s.

### 2.4 API tests
- Use `ApiHelper` or `BookingApi` instead of raw `request` calls once a test grows past one request.
- Validate responses against JSON schema with `schemaValidator` (AJV).
- Get auth tokens from the `bookerToken` fixture, not inside the test.

### 2.5 Parallel safety
- `fullyParallel: true` with 4 workers on CI. Tests must not share mutable server-side state.
- Use a worker-scoped user per worker for anything with a cart, profile or settings (see QAB-103).

### 2.6 Tags
Tag suites in the describe title: `@P0`, `@Regression`, `@e2e`, plus a feature tag such as `@Checkout`.
