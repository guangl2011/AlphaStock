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
  it("renders all fixture signals unfiltered, each with EN and ZH summaries", async () => {
    const html = await renderPage(FeedPage, { searchParams: Promise.resolve({}) });
    expect((html.match(/aria-label="signal SIG-/g) ?? []).length).toBe(10);
    expect(html).toContain("Signal Feed · 信号流");
  });

  it("filters by theme, category, and minimum strength", async () => {
    const themed = await renderPage(FeedPage, {
      searchParams: Promise.resolve({ theme: "Data Centers" }),
    });
    expect((themed.match(/aria-label="signal SIG-/g) ?? []).length).toBe(1);
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
    expect((html.match(/View signals · 查看信号/g) ?? []).length).toBe(7);
    // renderToString escapes entities — ampersands arrive as &amp;.
    expect(html).toContain("AI Power ·");
    expect(html).toContain("Grid &amp; Transmission ·");
    expect(html).toContain("Physical AI &amp; Robotics ·");
  });

  it("labels heat levels", async () => {
    const html = await renderPage(ThemesPage, {});
    expect(html).toContain("high heat");
  });
});

describe("/companies/[ticker]", () => {
  it("shows the published alert Alpha Score, current view, and timeline for VST", async () => {
    const html = await renderPage(CompanyPage, { params: Promise.resolve({ ticker: "VST" }) });
    expect(html).toContain("Vistra Corp.");
    expect(html).toContain("Alpha Score");
    expect(html).toContain("72");
    expect(html).toContain("3.5");
    expect(html).toContain("Current view · 当前观点");
    expect(html).toContain("Vistra raised forward capacity-price expectations");
    expect(html).toContain("Signal timeline · 信号时间线");
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
    expect(html).toContain("Track Record · 存证记录");
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
    expect(html).toContain("Weekly Alerts · 每周警报");
    expect(html).toContain("VST");
    expect(html).toContain("sent 2026-11-09");
    expect(html).toContain("pending owner approval · 未发送");
    expect(html).toContain("Symbotic");
  });
});
