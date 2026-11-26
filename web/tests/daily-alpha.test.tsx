/**
 * Daily Alpha digest tests (§7.2): four-section structure, the no-brief-today
 * fallback (most recent issue, clearly dated), the empty state, bilingual
 * single-language rendering, and the free-tier display rules (no internal
 * modeling numbers, qualitative Alpha Score tilt).
 */
process.env.ALPHASTOCK_DATA_SOURCE = "fixture";

import type { ReactElement } from "react";
import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { DailyAlphaDigest } from "../components/daily-alpha-digest";
import dailyAlphaFixture from "../fixtures/daily-alpha.json";
import {
  heatPercent,
  isFromToday,
  latestBrief,
  parseBriefs,
  scoreTilt,
} from "../lib/daily-alpha";
import { MESSAGES } from "../lib/messages";

const briefs = parseBriefs(dailyAlphaFixture);
const latest = latestBrief(briefs);
if (!latest) throw new Error("daily-alpha fixture must contain at least one brief");

const NOW = new Date("2026-11-26T09:00:00Z");
const TODAY = new Date(latest.asOf);

function render(element: ReactElement): string {
  return renderToString(element).replace(/<!-- -->/g, "");
}

function digest(locale: "en" | "zh", brief: Parameters<typeof DailyAlphaDigest>[0]["brief"], now = NOW): string {
  return render(
    <DailyAlphaDigest brief={brief} t={MESSAGES[locale]} locale={locale} now={now} />,
  );
}

describe("daily-alpha fixture + lib", () => {
  it("stages two briefs and picks the most recent by as_of", () => {
    expect(briefs).toHaveLength(2);
    expect(latest.asOf).toBe("2026-11-14T21:35:00Z");
    expect(latest.titleEn).toBe("Daily Alpha · 2026-11-14");
  });

  it("detects today's brief by UTC date only", () => {
    expect(isFromToday(latest.asOf, NOW)).toBe(false);
    expect(isFromToday(latest.asOf, TODAY)).toBe(true);
  });

  it("maps score deltas to qualitative tilts deterministically", () => {
    expect(scoreTilt(1.4)).toBe("improving");
    expect(scoreTilt(0.0)).toBe("steady");
    expect(scoreTilt(-0.8)).toBe("softening");
    expect(heatPercent(0, 0)).toBe(0);
    expect(heatPercent(4, 4)).toBe(100);
  });
});

describe("DailyAlphaDigest structure", () => {
  const html = digest("en", latest);

  it("renders the four sections: takeaway, signals, heat, macro", () => {
    for (const id of ["daily-alpha-takeaway", "daily-alpha-signals", "daily-alpha-heat", "daily-alpha-macro"]) {
      expect(html, id).toContain(`id="${id}"`);
    }
    // The takeaway paragraph, not the apostrophed heading — SSR escapes "'".
    expect(html).toContain("signals cleared the bar today");
    expect(html).toContain("Key signals");
    expect(html).toContain("Theme heat");
    expect(html).toContain("Macro in one sentence");
  });

  it("renders every brief item as a signal card", () => {
    expect((html.match(/aria-label="signal SIG-/g) ?? []).length).toBe(latest.items.length);
  });

  it("renders theme heat as counts and comparative bars", () => {
    expect(html).toContain("4 signals");
    expect(html).toContain('aria-label="Data Centers: 4 signals"');
  });
});

describe("DailyAlphaDigest fallback and empty states", () => {
  it("falls back to the most recent issue and labels its date when nothing is staged today", () => {
    const html = digest("en", latest);
    expect(html).toContain("No brief published today — showing the most recent issue (2026-11-14).");
  });

  it("shows no fallback banner when the brief is from today", () => {
    const html = digest("en", latest, TODAY);
    expect(html).not.toContain("No brief published today");
  });

  it("renders an explicit empty state when no brief exists", () => {
    const html = digest("en", null);
    expect(html).toContain("No Daily Alpha brief has been staged yet.");
    expect(html).not.toContain("as-page-title");
  });
});

describe("DailyAlphaDigest bilingual rendering", () => {
  it("renders exactly one language: EN brief text in English, ZH in 中文", () => {
    const en = digest("en", latest);
    expect(en).toContain("Daily Alpha · 2026-11-14");
    expect(en).toContain("4 signals cleared the bar today");
    expect(en).not.toMatch(/[\u4e00-\u9fff]/);

    const zh = digest("zh", latest);
    expect(zh).toContain("每日信号 · 2026-11-14");
    expect(zh).toContain("今日 4 条信号过线");
    expect(zh).toContain("主题热度");
    expect(zh).not.toContain("Today's takeaway");
  });
});
