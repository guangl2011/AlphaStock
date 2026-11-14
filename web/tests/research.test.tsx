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
  const byId = (id: string) => data.indices.find((i) => i.indexId === id)!;

  it("spans the three-year frame for all three indices", () => {
    expect(data.frame.start).toBe("2024-01-02");
    expect(data.indices.map((i) => i.indexId).sort()).toEqual(["AS-100", "AS-MCB100", "AS-MCG100"]);
    for (const index of data.indices) {
      expect(index.levels["2024-01-02"]).toBe(1000);
      for (const p of index.broadBenchmarks) expect(index.benchmarks[p]).toBeDefined();
    }
  });

  it("carries EN and 中文 caveats of equal length, never mixed", () => {
    expect(data.caveats.length).toBe(data.caveatsZh.length);
    expect(data.caveats.join(" ")).not.toMatch(/[一-鿿]/);
    expect(data.caveatsZh.every((c) => /[一-鿿]/.test(c))).toBe(true);
  });

  it("keeps AS-100 disjoint while MCG/MCB share 70 names", () => {
    expect(data.universeOverlap).toEqual([]);
    expect(data.mixedCapShared).toBe(70);
  });

  it("gates late listings on sourced regular-way dates", () => {
    expect(byId("AS-MCG100").entries).toContainEqual({ date: "2024-06-28", ticker: "SERV" });
    expect(byId("AS-MCB100").entries).toContainEqual({ date: "2024-06-28", ticker: "OKLO" });
    expect(data.listingDates.SERV).toBe("2024-04-18");
    expect(data.listingDates.XE).toBe("2026-04-24");
  });

  it("prices Everpure as P in AS-100 with no exclusions", () => {
    const large = byId("AS-100");
    expect(large.excluded).toEqual([]);
    expect(large.corporateEvents.map((e) => e.ticker)).toContain("P");
  });

  it("aligns chart series on the index's trading dates", () => {
    const { dates, series } = chartSeries(byId("AS-MCB100"));
    expect(series[0].values).toHaveLength(dates.length);
    expect(series.map((s) => s.key)).toEqual(["AS-MCB100", "SPY", "IJH", "IWM"]);
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

describe("/research page (English)", () => {
  it("leads with caveats, then all three indices with stats and categories", async () => {
    const html = await render();
    expect(html).toContain("Research backfill");
    expect(html.indexOf("Read before the numbers")).toBeLessThan(html.indexOf("Total return"));
    expect(html).toContain("survivorship");
    expect(html).toContain("AS-100 — Large-Cap 100");
    expect(html).toContain("AS-MCG100 — Mixed-Cap-Growth 100");
    expect(html).toContain("AS-MCB100 — Mixed-Cap-Balanced 100");
    expect(html).toContain("Category sub-indices");
    expect(html).toContain("SERV (2024-06-28)");
    expect(html).toContain("2026 YTD");
    expect(html).not.toMatch(/[一-鿿]/);
  });

  it("lists You.com-researched corporate events with sources and checks", async () => {
    const html = await render();
    expect(html).toContain("researched via You.com");
    expect(html).toContain("reverse split");
    expect(html).toContain("adjusted (");
    expect(html).toContain("nasdaq.com");
  });
});
