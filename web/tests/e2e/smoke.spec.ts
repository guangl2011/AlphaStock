import { expect, test } from "@playwright/test";
import sentimentFixture from "../../fixtures/sentiment.json";

/** Derived sentiment values — the fixture is the source of truth. */
const redditCoverage = (sentimentFixture as {
  coverage: { reddit: { kept: number; keep_target: number } };
}).coverage.reddit;
const feedbackRows = (sentimentFixture as { posts: Array<{ topic_id: string }> }).posts.filter(
  (post) => post.topic_id === "ai-power-demand",
).length;

/**
 * Smoke coverage for the eight Explorer routes against the fixture-mode
 * production build, plus the free-tier display rules rendered end to end.
 */

const mainRoutes = [
  "/",
  "/feed",
  "/themes",
  "/indices",
  "/research",
  "/factors",
  "/sentiment",
  "/track-record",
  "/alerts",
  "/companies",
  "/reports",
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
  await expect(main).toContainText("Sources");

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
  await expect(page.locator("main")).toContainText("Grid & Transmission");
  await expect(page.locator("main")).toContainText("high heat");
  await expect(page.locator(".as-theme-link")).toHaveCount(7);
});

test("/indices navigates from the home page and shows index levels", async ({ page }) => {
  await page.goto("/");
  await page.locator('nav a[href="/indices"]').click();
  await expect(page).toHaveURL(/\/indices$/);
  await expect(page.locator("h1")).toContainText("Indices");
  await expect(page.locator('section[aria-label="index AS-100"]')).toContainText("1,006.7");
  // The mixed-cap indices are single-point (base 2026-11-13 only) — an honest 0.0% since base.
  await expect(page.locator('section[aria-label="index AS-MCG100"]')).toContainText("1,000.0");
  await expect(page.locator('section[aria-label="index AS-MCB100"]')).toContainText("0.0%");
  // PSTG was mapped to P (2026-11-14): every constituent resolves, no footnote.
  await expect(page.locator("main")).not.toContainText("is excluded from");
});

test("/companies/VST shows Alpha Score, current view, and timeline", async ({ page }) => {
  await page.goto("/companies/VST");
  await expect(page.locator("h1")).toContainText("VST");
  await expect(page.locator("main")).toContainText("Alpha Score");
  await expect(page.locator("main")).toContainText("Current view");
  await expect(page.locator("main")).toContainText("Signal timeline");
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
  await expect(page.locator("main")).toContainText("Weekly Alerts");
  await expect(page.locator("main")).toContainText("pending owner approval — not sent");
});

test("/companies lists the covered universe and links detail pages", async ({ page }) => {
  await page.goto("/companies");
  await expect(page.locator("main")).toContainText("Constellation Energy");
  await page.locator('main a[href="/companies/VST"]').click();
  await expect(page).toHaveURL(/\/companies\/VST$/);
  await expect(page.locator("h1")).toContainText("VST");
});

test("/reports indexes published weeks with the aggregate report link", async ({ page }) => {
  await page.goto("/reports");
  await expect(page.locator("main")).toContainText("Week of 2026-11-09");
  await expect(page.locator("main")).toContainText("Week of 2026-11-02");
  await expect(page.locator('main a[href="/weekly/report.md"]').first()).toBeVisible();
});

test("the sidebar collapses, persists the choice, and restores it after reload", async ({ page }) => {
  await page.goto("/");
  await page.locator("aside.as-sidebar .as-rail-toggle").click();
  await expect(page.locator("aside.as-sidebar")).toHaveClass(/as-collapsed/);
  await page.reload();
  // The stored preference applies after hydration.
  await expect(page.locator("aside.as-sidebar")).toHaveClass(/as-collapsed/);
  await page.locator("aside.as-sidebar .as-rail-toggle").click();
  await expect(page.locator("aside.as-sidebar")).not.toHaveClass(/as-collapsed/);
});

test("the account menu opens, shows the placeholder actions, and closes on Escape", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator(".as-account-popover")).toHaveCount(0);
  await page.locator("button.as-account-toggle").click();
  const popover = page.locator(".as-account-popover");
  await expect(popover).toBeVisible();
  await expect(popover).toContainText("researcher@gridcapital.net");
  await expect(popover).toContainText("Light mode");
  await expect(popover).toContainText("Coming soon");
  await expect(popover.locator("button:disabled")).toHaveCount(3);
  await page.keyboard.press("Escape");
  await expect(page.locator(".as-account-popover")).toHaveCount(0);
});

test("the account menu stays clickable on the collapsed rail", async ({ page }) => {
  await page.goto("/");
  await page.locator("aside.as-sidebar .as-rail-toggle").click();
  await page.locator("button.as-account-toggle").click();
  // Regression: the rail's overflow used to swallow clicks on the popup.
  await page.locator(".as-lang-switch").click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-Hans");
});

// The display rule covers signal-bearing pages. /research and /factors are
// statistical research views whose β, correlations and CAGR/Vol ratios are
// two-decimal by design — they carry no scenario ranges or probabilities.
const signalRoutes = mainRoutes.filter((r) => r !== "/research" && r !== "/factors");

test("public pages never leak numeric scenario ranges or probabilities", async ({ page }) => {
  for (const route of signalRoutes) {
    await page.goto(route);
    // Live post snippets quote market prices like "$187.62" — user content,
    // not modeling output — so the check skips the sentiment feedback list.
    const text = await page.locator("main").evaluate((el) => {
      el.querySelector('section[aria-labelledby="sentiment-feedback"]')?.remove();
      return (el as HTMLElement).innerText;
    });
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

test("language switch renders one language at a time and keeps the page", async ({ page }) => {
  await page.goto("/indices");
  await expect(page.locator("h1")).toHaveText("Indices");
  // The switch lives in the account popup.
  await page.locator("button.as-account-toggle").click();
  await page.locator(".as-lang-switch").click();
  await expect(page).toHaveURL(/\/indices$/);
  await expect(page.locator("h1")).toHaveText("指数");
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-Hans");
  await expect(page.locator("main")).not.toContainText("Demo data");
  await page.locator("button.as-account-toggle").click();
  await page.locator(".as-lang-switch").click();
  await expect(page.locator("h1")).toHaveText("Indices");
});

test("language switch keeps the current path and query string", async ({ page }) => {
  await page.goto("/themes?year=2026&quarter=Q3");
  await expect(page.locator("h1")).toHaveText("Theme Explorer");
  await page.locator("button.as-account-toggle").click();
  await page.locator(".as-lang-switch").click();
  // Same page, same period selection, other language.
  await expect(page).toHaveURL(/\/themes\?year=2026&quarter=Q3$/);
  await expect(page.locator("h1")).toHaveText("主题热度");
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-Hans");
});

test("/sentiment renders topic cards and ?topic= switching works", async ({ page }) => {
  await page.goto("/sentiment");
  await expect(page.locator("h1")).toHaveText("Market Sentiment");
  await expect(page.locator("main")).toContainText("What this scans and how it scores");
  await expect(page.locator("main")).toContainText("site:linkedin.com");
  await expect(page.locator(".as-heat-card")).toHaveCount(6);
  // v2 sampling: per-platform coverage summary and n= chips on the cards.
  await expect(page.locator("main")).toContainText("Sampling coverage (this scan)");
  await expect(page.locator("main")).toContainText(
    `n=${redditCoverage.kept} of ${redditCoverage.keep_target} unique samples`,
  );
  await expect(page.locator(".as-platform-chip").first()).toContainText("n=");

  // Select a topic by card link; the feedback section follows the selection.
  await page.locator('a[href="/sentiment?topic=ai-power-demand"]').click();
  await expect(page).toHaveURL(/\/sentiment\?topic=ai-power-demand$/);
  await expect(page.locator("main")).toContainText("Recent feedback — AI power demand");
  await expect(page.locator(".as-feedback-row")).toHaveCount(feedbackRows);
  // Per-post confidence bands surface in the sourced feedback rows.
  await expect(page.locator(".as-feedback-row").first()).toContainText("Confidence:");

  // The live fixture has no empty topic; the previously-empty topic now
  // renders sourced feedback through the same topic-selection path.
  await page.goto("/sentiment?topic=reit-leasing");
  await expect(page.locator("main")).toContainText("Recent feedback — REIT data-center leasing");
});
