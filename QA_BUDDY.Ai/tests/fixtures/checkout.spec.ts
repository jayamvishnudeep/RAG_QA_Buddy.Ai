import { test, expect } from '@playwright/test';

test.describe('Checkout', () => {
    test.beforeEach(async ({ page }) => {
        await page.goto('/');
    });

    test('completes an order', async ({ page }) => {
        await page.getByRole('button', { name: 'Checkout' }).click();
        await expect(page.getByText('Thank you')).toBeVisible();
    });

    test('shows the cart badge', async ({ page }) => {
        await expect(page.locator('.cart-badge')).toHaveText('1');
    });
});
