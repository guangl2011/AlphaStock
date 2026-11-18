/**
 * Server-render smoke tests for the five Explorer routes against fixtures —
 * no live Supabase, no runtime LLM. Each test awaits the async server
 * component and renders it with react-dom/server.
 */
process.env.ALPHASTOCK_DATA_SOURCE = "fixture";

import type { ReactElement } from "react";
import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";

import AlertsPage from "../app/alerts/page";
import CompanyPage from "../app/companies/[ticker]/page";
import FeedPage from "../app/feed/page";
import SentimentPage from "../app/sentiment/page";
import ThemesPage from "../app/themes/page";
import TrackRecordPage from "../app/track-record/page";
import signalsFixture from "../fixtures/signals.json";

/** Expected fixture count for a category filter — derived, not hardcoded. */
const v2Count = (signalsFixture as Array<{ category: string }>).filter(
  (row) => row.category === "V2_INTERCONNECTION",
).length;

async function renderPage(component: unknown, props: unknown): Promise<string> {
  // The pages are async server components: awaiting yields the JSX tree that
  // Next would stream, so rendering it exercises the real data + render path.
  const page = component as (p: never) => Promise<ReactElement>;
  // React separates adjacent text nodes with comment markers in SSR output;
  // strip them so substring assertions read like the rendered text.
  return renderToString(await page(props as never)).replace(/<!-- -->/g, "");
}

describe("/feed", () => {
  it("renders all fixture signals unfiltered, in English only by default", async () => {
    const html = await renderPage(FeedPage, { searchParams: Promise.resolve({}) });
    expect((html.match(/aria-label="signal SIG-/g) ?? []).length).toBe(14);
    expect(html).toContain("Signal Feed");
    expect(html).not.toMatch(/[\u4e00-\u9fff]/);
  });

  it("filters by theme, category, and minimum strength", async () => {
    const themed = await renderPage(FeedPage, {
      searchParams: Promise.resolve({ theme: "Data Centers" }),
    });
    expect((themed.match(/aria-label="signal SIG-/g) ?? []).length).toBe(5);
    expect(themed).toContain("Loudoun");

    const strong = await renderPage(FeedPage, {
      searchParams: Promise.resolve({ strength: "5" }),
    });
    expect((strong.match(/aria-label="signal SIG-/g) ?? []).length).toBe(1);

    const vertical = await renderPage(FeedPage, {
      searchParams: Promise.resolve({ category: "V2_INTERCONNECTION" }),
    });
    expect(v2Count).toBeGreaterThan(0);
    expect((vertical.match(/aria-label="signal SIG-/g) ?? []).length).toBe(v2Count);
  });

  it("shows an empty state when no signal matches, never a blank page", async () => {
    const html = await renderPage(FeedPage, {
      searchParams: Promise.resolve({ theme: "Utilities", strength: "5" }),
    });
    expect(html).toContain("No signals match these filters");
  });

  it("marks fixture renders as demo data", async () => {
    const html = await renderPage(FeedPage, { searchParams: Promise.resolve({}) });
    expect(html).toContain("Demo data");
  });
});

describe("/themes", () => {
  it("renders all seven themes including empty ones", async () => {
    const html = await renderPage(ThemesPage, {});
    expect((html.match(/View signals →/g) ?? []).length).toBe(7);
    // renderToString escapes entities — ampersands arrive as &amp;.
    expect(html).toContain("AI Power");
    expect(html).toContain("Grid &amp; Transmission");
    expect(html).toContain("Physical AI &amp; Robotics");
  });

  it("labels heat levels", async () => {
    const html = await renderPage(ThemesPage, {});
    expect(html).toContain("high heat");
  });

  it("renders the yearly summary with the YTD label by default and for ?year=2026", async () => {
    for (const searchParams of [undefined, Promise.resolve({ year: "2026" })]) {
      const html = await renderPage(ThemesPage, { searchParams });
      expect(html).toContain("Yearly summary");
      expect(html).toContain("2026 market findings — year to date");
      expect(html).toContain("Year to date");
      expect(html).toContain("Applied Digital aggregates ~$36B");
    }
  });

  it("renders the quarterly summary for ?year=2026&quarter=Q2", async () => {
    const html = await renderPage(ThemesPage, {
      searchParams: Promise.resolve({ year: "2026", quarter: "Q2" }),
    });
    expect(html).toContain("Quarterly summary");
    expect(html).toContain("2026 Q2 market findings");
    expect(html).toContain("Delta Forge 1 signed");
  });

  it("falls back to the most recent quarter on an unknown or absent quarter", async () => {
    for (const searchParams of [
      undefined,
      Promise.resolve({ quarter: "Q9" }),
    ]) {
      const html = await renderPage(ThemesPage, { searchParams });
      expect(html).toContain("2026 Q3 market findings");
      expect(html).toContain("Vertiv Q3 2026 orders");
    }
  });

  it("picker links target the correct search-param URLs", async () => {
    const html = await renderPage(ThemesPage, { searchParams: Promise.resolve({}) });
    expect(html).toContain('href="/themes?year=2026"');
    // Ampersands arrive escaped in renderToString output.
    expect(html).toContain('href="/themes?year=2026&amp;quarter=Q1"');
    expect(html).toContain('href="/themes?year=2026&amp;quarter=Q3"');
    expect(html).not.toContain('quarter=Q4"');
  });

  it("marks fixture renders with the demo-data notice and curated-content label", async () => {
    const html = await renderPage(ThemesPage, { searchParams: Promise.resolve({}) });
    expect(html).toContain("Demo data");
    expect(html).toContain("Curated market findings");
  });

  it("shows the empty state for an unresearched year, never a blank section", async () => {
    const html = await renderPage(ThemesPage, { searchParams: Promise.resolve({ year: "2025" }) });
    expect(html).toContain("No findings recorded for this period yet.");
    // The quarterly section still falls back to the most recent quarter.
    expect(html).toContain("2026 Q3 market findings");
  });
});

describe("/companies/[ticker]", () => {
  it("shows the published alert Alpha Score, current view, and timeline for VST", async () => {
    const html = await renderPage(CompanyPage, { params: Promise.resolve({ ticker: "VST" }) });
    expect(html).toContain("Vistra Corp.");
    expect(html).toContain("Alpha Score");
    expect(html).toContain("72");
    expect(html).toContain("3.5");
    expect(html).toContain("Current view");
    expect(html).toContain("Vistra raised forward capacity-price expectations");
    expect(html).toContain("Signal timeline");
    expect(html).toContain("Forward capacity prices retrace");
  });

  it("normalizes lowercase tickers", async () => {
    const html = await renderPage(CompanyPage, { params: Promise.resolve({ ticker: "vst" }) });
    expect(html).toContain("Vistra Corp.");
  });

  it("404s for a ticker outside coverage", async () => {
    let digest: string | undefined;
    try {
      await renderPage(CompanyPage, { params: Promise.resolve({ ticker: "NOPE" }) });
    } catch (error) {
      digest = (error as { digest?: string }).digest;
    }
    // Next 15 reports notFound() as NEXT_NOT_FOUND in app context and as
    // NEXT_HTTP_ERROR_FALLBACK;404 when rendered outside it.
    expect(digest).toMatch(/NEXT_NOT_FOUND|NEXT_HTTP_ERROR_FALLBACK/);
  });
});

describe("/track-record", () => {
  it("lists attestation batches with hashes and the attested signals", async () => {
    const html = await renderPage(TrackRecordPage, {});
    expect(html).toContain("Track Record");
    expect(html).toContain("batch-2026-11-09");
    expect(html).toContain("batch-2026-11-02");
    expect((html.match(/[0-9a-f]{64}/g) ?? []).length).toBeGreaterThanOrEqual(3);
    expect(html).toContain("SIG-2026-11-000121");
  });

  it("keeps losing (negative-direction) signals visible", async () => {
    const html = await renderPage(TrackRecordPage, {});
    expect(html).toContain("negative-direction (losing)");
    expect(html).toContain("withdrew from the CAISO interconnection queue");
    expect(html).toContain("▼ negative");
  });
});

describe("/alerts", () => {
  it("renders the alert history with sent and pending states", async () => {
    const html = await renderPage(AlertsPage, {});
    expect(html).toContain("Weekly Alerts");
    expect(html).toContain("VST");
    expect(html).toContain("sent 2026-11-09");
    expect(html).toContain("pending owner approval — not sent");
    expect(html).toContain("Symbotic");
  });
});

describe("/sentiment", () => {
  it("renders all six topic cards with band labels, confidence, and the scan timestamp", async () => {
    const html = await renderPage(SentimentPage, {});
    expect(html).toContain("Market Sentiment");
    // Every topic from the latest run is a card, including the zero-post one.
    expect((html.match(/\/sentiment\?topic=/g) ?? []).length).toBe(6);
    expect(html).toContain("Data center capex");
    expect(html).toContain("REIT data-center leasing");
    expect(html).toContain("Bullish");
    expect(html).toContain("−2 vs previous scan");
    expect(html).toContain("Demo data");
    // Free-tier display rules: no two-decimal modeling numbers in the visible
    // text (tags stripped — inline style attributes legitimately contain them).
    expect(html.replace(/<[^>]+>/g, " ")).not.toMatch(/[+-]?\d+\.\d{2}\b/);
  });

  it("selects a topic via ?topic= and renders its sourced feedback", async () => {
    const html = await renderPage(SentimentPage, {
      searchParams: Promise.resolve({ topic: "ai-power-demand" }),
    });
    expect(html).toContain("Recent feedback — AI power demand");
    expect(html).toContain("u/voltage_diaries");
    expect(html).toContain("LinkedIn (search-indexed)");
    expect(html).toContain('rel="noopener noreferrer"');
  });

  it("falls back to the first topic on an unknown id and shows the empty state for the zero-post topic", async () => {
    const unknown = await renderPage(SentimentPage, {
      searchParams: Promise.resolve({ topic: "not-a-topic" }),
    });
    expect(unknown).toContain("Recent feedback — Data center capex");

    const empty = await renderPage(SentimentPage, {
      searchParams: Promise.resolve({ topic: "reit-leasing" }),
    });
    expect(empty).toContain("No posts recorded for this topic in the latest scan.");
  });
});
