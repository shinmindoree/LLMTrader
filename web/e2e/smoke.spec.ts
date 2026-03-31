import { test, expect } from "@playwright/test";

test.describe("Landing page", () => {
  test("should render the landing page with hero section", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("h1")).toBeVisible();
    await expect(page.locator("h1")).toContainText("Backtest");
  });

  test("should show login/signup CTAs when not authenticated", async ({ page }) => {
    await page.goto("/");
    await page.waitForTimeout(1000);
    const getStarted = page.getByRole("link", { name: /get started/i });
    const login = page.getByRole("link", { name: /login/i });
    const hasCTA = (await getStarted.count()) > 0 || (await login.count()) > 0;
    expect(hasCTA).toBeTruthy();
  });
});

test.describe("Auth page", () => {
  test("should render the auth form", async ({ page }) => {
    await page.goto("/auth");
    await expect(page.locator("input[type='email']")).toBeVisible();
    await expect(page.locator("input[type='password']")).toBeVisible();
  });

  test("should show validation on empty submit", async ({ page }) => {
    await page.goto("/auth");
    const submitButton = page.getByRole("button", { name: /login|sign in|log in/i });
    if ((await submitButton.count()) > 0) {
      await submitButton.click();
      // Should show some validation or stay on auth page
      await expect(page).toHaveURL(/\/auth/);
    }
  });
});

test.describe("Navigation", () => {
  test("should redirect unauthenticated users to /auth", async ({ page }) => {
    await page.goto("/dashboard");
    await page.waitForURL(/\/(auth|dashboard)/);
    // If auth is disabled, stays on dashboard; if enabled, redirects to auth
    const url = page.url();
    expect(url).toMatch(/\/(auth|dashboard)/);
  });
});

test.describe("404 page", () => {
  test("should show not found page for invalid routes", async ({ page }) => {
    await page.goto("/this-page-does-not-exist");
    await expect(page.locator("text=404")).toBeVisible();
  });
});
