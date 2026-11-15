/**
 * /research backfill view against the committed pipeline-generated fixture.
 */
process.env.ALPHASTOCK_DATA_SOURCE = "fixture";

import type { ReactElement } from "react";
import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";

import ResearchPage from "../app/research/page";
import researchFixture from "../fixtures/research.json";
import { MESSAGES as messages } from "../lib/messages";
import {
  chartSeries,
  niceTicks,
  parseResearchFixture,
  pct,
  pctAbs,
  resolvePeriod,
} from "../lib/research";

type PageProps = { searchParams?: Promise<Record<string, string | string[] | undefined>> };

async function render(period?: string): Promise<string> {
  const page = ResearchPage as unknown as (props: PageProps) => Promise<ReactElement>;
  const props = period ? { searchParams: Promise.resolve({ period }) } : {};
  return renderToString(await page(props)).replace(/<!-- -->/g, "");
}

describe("research fixture", () => {
  const data = parseResearchFixture(researchFixture);
  const byId = (id: string) => data.indices.find((i) => i.indexId === id)!;

  it("spans the 2020-01-02 frame for all three indices", () => {
    expect(data.frame.start).toBe("2020-01-02");
    expect(data.indices.map((i) => i.indexId).sort()).toEqual(["AS-100", "AS-MCB100", "AS-MCG100"]);
    for (const index of data.indices) {
      expect(index.levels["2020-01-02"]).toBe(1000);
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
    // SPAC trust-shell history is never counted before the de-SPAC date.
    expect(data.listingDates.HYLN).toBe("2020-10-02");
    expect(byId("AS-MCG100").entries).toContainEqual({ date: "2020-12-31", ticker: "HYLN" });
  });

  it("carries selectable periods for every index, measured from the prior close", () => {
    for (const index of data.indices) {
      const ids = index.periods.map((p) => p.id);
      expect(ids.slice(0, 5)).toEqual(["max", "5y", "3y", "1y", "ytd"]);
      expect(ids).toEqual(expect.arrayContaining(["2020", "2021", "2022", "2023", "2024", "2025"]));
      const y2022 = index.periods.find((p) => p.id === "2022")!;
      expect(y2022.base).toBe("2021-12-31");
      expect(y2022.end).toBe("2022-12-30");
      expect(y2022.stats.totalReturn).toBeCloseTo(index.stats.calendarReturns["2022"], 6);
      expect(Object.keys(y2022.stats.calendarReturns)).toEqual(["2022"]);
      expect(y2022.categories.length).toBeGreaterThan(0);
      expect(resolvePeriod(index, "max").stats.totalReturn).toBe(index.stats.totalReturn);
    }
  });

  it("falls back to the full frame for an unknown period", () => {
    expect(resolvePeriod(byId("AS-100"), "nope").id).toBe("max");
    expect(resolvePeriod(byId("AS-100"), undefined).id).toBe("max");
  });

  it("cuts and rebases the chart to the selected period", () => {
    const index = byId("AS-100");
    const p = resolvePeriod(index, "2023");
    const { dates, series } = chartSeries(index, p);
    expect(dates[0]).toBe(p.base);
    expect(dates[dates.length - 1]).toBe(p.end);
    for (const s of series) expect(s.values[0]).toBe(1000);
    const last = series[0].values[series[0].values.length - 1]!;
    expect(last / 1000 - 1).toBeCloseTo(p.stats.totalReturn, 3);
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

describe("period labels", () => {
  it("names periods in each language without mixing", () => {
    expect(messages.en.research.periodName("max", "2020")).toBe("Since 2020");
    expect(messages.en.research.periodName("5y", "2020")).toBe("5Y");
    expect(messages.en.research.periodName("ytd", "2020")).toBe("YTD");
    expect(messages.zh.research.periodName("max", "2020")).toBe("2020 年至今");
    expect(messages.zh.research.periodName("3y", "2020")).toBe("近 3 年");
    expect(messages.zh.research.periodName("2023", "2020")).toBe("2023 年");
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

  it("renders a period selector in every index section, Since 2020 selected by default", async () => {
    const html = await render();
    expect(html.match(/aria-label="Time period for /g)).toHaveLength(3);
    expect(html).toMatch(/aria-current="true"[^>]*>Since 2020</);
    expect(html).toContain('href="/research?period=2022"');
    expect(html).toContain('href="/research?period=5y"');
  });

  it("switches KPIs, chart, tables and events to the chosen period", async () => {
    const html = await render("2022");
    expect(html).toMatch(/aria-current="true"[^>]*>2022</);
    expect(html).toContain("Risk &amp; return, 2021-12-31 → 2022-12-30");
    expect(html).toContain("Measured from the 2021-12-31 close to 2022-12-30");
    expect(html).not.toContain("2026 YTD");
    // Events are limited to the period: 2022 splits in, 2024 splits out.
    expect(html).toContain("20-for-1 forward split");
    expect(html).not.toContain("10-for-1 forward split");
  });

  it("drops calendar-year columns for trailing windows", async () => {
    const html = await render("3y");
    expect(html).toContain("Risk &amp; return, 2023-11-13 → 2026-11-13");
    expect(html).not.toContain("2026 YTD");
  });

  it("lists You.com-researched corporate events with sources and checks", async () => {
    const html = await render();
    expect(html).toContain("researched via You.com");
    expect(html).toContain("reverse split");
    expect(html).toContain("adjusted (");
    expect(html).toContain("nasdaq.com");
  });
});
