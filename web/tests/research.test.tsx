/**
 * /research backfill view against the committed pipeline-generated fixture.
 */
process.env.ALPHASTOCK_DATA_SOURCE = "fixture";

import type { ReactElement } from "react";
import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";

import ResearchPage from "../app/research/page";
import researchFixture from "../fixtures/research.json";
import { chartSeries, niceTicks, parseResearchFixture, pct, pctAbs } from "../lib/research";

async function render(): Promise<string> {
  const page = ResearchPage as unknown as () => Promise<ReactElement>;
  return renderToString(await page()).replace(/<!-- -->/g, "");
}

describe("research fixture", () => {
  const data = parseResearchFixture(researchFixture);

  it("spans the three-year frame from the first trading day of 2024", () => {
    expect(data.frame.start).toBe("2024-01-02");
    expect(data.indices.map((i) => i.indexId)).toEqual(["AS-100", "AS-MC100"]);
    for (const index of data.indices) {
      expect(index.levels["2024-01-02"]).toBe(1000);
      for (const p of index.broadBenchmarks) expect(index.benchmarks[p]).toBeDefined();
    }
  });

  it("gates GEV on its regular-way listing, not when-issued prints", () => {
    const mixed = data.indices.find((i) => i.indexId === "AS-100")!;
    expect(mixed.entries).toContainEqual({ date: "2024-06-28", ticker: "GEV" });
    expect(data.listingDates.GEV).toBe("2024-04-02");
  });

  it("prices Everpure as P with no exclusions", () => {
    const mc = data.indices.find((i) => i.indexId === "AS-MC100")!;
    expect(mc.excluded).toEqual([]);
    expect(mc.corporateEvents.map((e) => e.ticker)).toContain("P");
  });

  it("aligns chart series on the index's trading dates", () => {
    const { dates, series } = chartSeries(data.indices[0]);
    expect(series[0].values).toHaveLength(dates.length);
    expect(series.map((s) => s.key)).toEqual(["AS-100", "SPY", "IWM", "IWC"]);
  });

  it("rejects a malformed fixture loudly", () => {
    expect(() => parseResearchFixture({ frame: {} })).toThrow();
  });
});

describe("research helpers", () => {
  it("formats signed and unsigned percents", () => {
    expect(pct(0.1234)).toBe("+12.3%");
    expect(pct(-0.309)).toBe("−30.9%");
    expect(pct(0.0001)).toBe("0.0%");
    expect(pctAbs(0.288)).toBe("28.8%");
  });

  it("produces nice ticks covering the range", () => {
    const t = niceTicks(830, 2650, 5);
    expect(t[0]).toBeLessThanOrEqual(830);
    expect(t[t.length - 1]).toBeGreaterThanOrEqual(2600);
  });
});

describe("/research page", () => {
  it("leads with caveats, then both indices with stats and categories", async () => {
    const html = await render();
    expect(html).toContain("Research backfill · 三年回溯研究");
    expect(html.indexOf("Read before the numbers")).toBeLessThan(html.indexOf("Total return"));
    expect(html).toContain("survivorship");
    expect(html).toContain("AS-100 — Mixed universe equal-weight");
    expect(html).toContain("AS-MC100 — Large-Cap 100 equal-weight");
    expect(html).toContain("Category sub-indices");
    expect(html).toContain("GEV (2024-06-28)");
    expect(html).toContain("2026 YTD");
  });

  it("lists You.com-researched corporate events with sources and checks", async () => {
    const html = await render();
    expect(html).toContain("Corporate events researched via You.com");
    expect(html).toContain("OKLO (2024-06-28)");
    expect(html).toContain("ticker change");
    expect(html).toContain("adjusted (");
    expect(html).toContain("nasdaq.com");
  });
});
