import { expect, test } from "@playwright/test";

/**
 * Smoke coverage for the six Explorer routes against the fixture-mode
 * production build, plus the free-tier display rules rendered end to end.
 */

const mainRoutes = [
  "/",
  "/feed",
  "/themes",
  "/indices",
  "/track-record",
  "/alerts",
] as const;

test("every main route answers 200 with a visible heading", async ({ page }) => {
  for (const route of mainRoutes) {
    const response = await page.goto(route);
    expect(response?.status(), route).toBe(200);
    await expect(page.locator("h1"), route).toBeVisible();
  }
});

test("/feed renders the signal stream with filters", async ({ page }) => {
  await page.goto("/feed");
  await expect(page.locator("article.as-card").first()).toBeVisible();
  const main = page.locator("main");
  await expect(main).toContainText("Invalidation condition");
  await expect(main).toContainText("Sources · 来源");

  // The strength filter narrows the stream.
  await page.goto("/feed?strength=5");
  await expect(page.locator("article.as-card")).toHaveCount(1);
  await expect(main).toContainText("Loudoun County");
});

test("/feed shows an explicit empty state for unmatched filters", async ({ page }) => {
  await page.goto("/feed?theme=Utilities&strength=5");
  await expect(page.locator("main")).toContainText("No signals match these filters");
});

test("/themes shows all seven themes with heat", async ({ page }) => {
  await page.goto("/themes");
  await expect(page.locator("main")).toContainText("Grid & Transmission ·");
  await expect(page.locator("main")).toContainText("high heat");
  await expect(page.locator(".as-theme-link")).toHaveCount(7);
});

test("/indices navigates from the home page and shows index levels", async ({ page }) => {
  await page.goto("/");
  await page.locator('nav a[href="/indices"]').click();
  await expect(page).toHaveURL(/\/indices$/);
  await expect(page.locator("h1")).toContainText("Indices · 指数对比");
  await expect(page.locator('section[aria-label="index AS-100"]')).toContainText("1,055.5");
  // AS-MC100 is still single-point (base date only) — an honest 0.0% since base.
  await expect(page.locator('section[aria-label="index AS-MC100"]')).toContainText("1,000.0");
  await expect(page.locator('section[aria-label="index AS-MC100"]')).toContainText("0.0%");
  // Documented exclusion shows as a footnote, not an error.
  await expect(page.locator("main")).toContainText("PSTG is excluded from AS-MC100");
});

test("/companies/VST shows Alpha Score, current view, and timeline", async ({ page }) => {
  await page.goto("/companies/VST");
  await expect(page.locator("h1")).toContainText("VST");
  await expect(page.locator("main")).toContainText("Alpha Score");
  await expect(page.locator("main")).toContainText("Current view · 当前观点");
  await expect(page.locator("main")).toContainText("Signal timeline · 信号时间线");
});

test("/companies/UNKNOWN-404s", async ({ page }) => {
  const response = await page.goto("/companies/NOPE");
  expect(response?.status()).toBe(404);
});

test("/track-record shows attestation batches and losing signals", async ({ page }) => {
  await page.goto("/track-record");
  await expect(page.locator("main")).toContainText("batch-2026-11-09");
  await expect(page.locator("main")).toContainText("negative-direction (losing)");
  // A full SHA-256 hash must be visible — the tamper-evident record.
  await expect(page.locator("code").filter({ hasText: /^[0-9a-f]{64}$/ }).first()).toBeVisible();
});

test("/alerts shows sent and pending-approval states", async ({ page }) => {
  await page.goto("/alerts");
  await expect(page.locator("main")).toContainText("Weekly Alerts · 每周警报");
  await expect(page.locator("main")).toContainText("pending owner approval · 未发送");
});

test("public pages never leak numeric scenario ranges or probabilities", async ({ page }) => {
  for (const route of mainRoutes) {
    await page.goto(route);
    const text = await page.locator("main").innerText();
    // Internal modeling numbers carry two decimals (-0.06, 0.61); visible
    // market data is integer scores, MW, or one-decimal percentages.
    expect(text, route).not.toMatch(/[+-]?\d+\.\d{2}\b/);
  }
});

test("navigation links every main route", async ({ page }) => {
  await page.goto("/");
  for (const route of mainRoutes.slice(1)) {
    await expect(page.locator(`nav a[href="${route}"]`)).toBeVisible();
  }
});
