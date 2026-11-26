import { expect, test } from "@playwright/test";

/**
 * Login-flow smoke in fixture mode: the standalone login page with its
 * honest "enter" action, the demo session it starts, and sign-out back to
 * /login. Zero Supabase network — the run stays offline like the rest of CI.
 */
test("/login renders the fixture entry and lands on /feed", async ({ page }) => {
  await page.goto("/login");
  await expect(page.locator("h1")).toHaveText("Sign in");
  await expect(page.locator("main, body")).toContainText("Fixture mode: demo data, no live sign-in.");
  // Standalone page — the sidebar rail is not part of the login shell.
  await expect(page.locator("aside.as-sidebar")).toHaveCount(0);

  await page.locator("button.as-login-submit").click();
  await expect(page).toHaveURL(/\/feed$/);
  await expect(page.locator("aside.as-sidebar")).toBeVisible();
});

test("sign out from the account menu returns to /login", async ({ page }) => {
  await page.goto("/feed");
  await page.locator("button.as-account-toggle").click();
  await page.locator(".as-account-popover form button", { hasText: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.locator("button.as-login-submit")).toBeVisible();
});
