/**
 * Server-render and unit tests for the /indices view against the committed
 * pipeline-generated fixture — no live Supabase, no chart dependency.
 */
process.env.ALPHASTOCK_DATA_SOURCE = "fixture";

import type { ReactElement } from "react";
import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";

import IndicesPage from "../app/indices/page";
import indexFixture from "../fixtures/index.json";
import {
  formatLevel,
  formatReturnPct,
  levelDates,
  parseIndexFixture,
  sinceBaseReturn,
  sparklinePoints,
} from "../lib/indices";

async function renderPage(component: unknown, props: unknown): Promise<string> {
  const page = component as (p: never) => Promise<ReactElement>;
  return renderToString(await page(props as never)).replace(/<!-- -->/g, "");
}

describe("/indices page", () => {
  it("renders both indices with levels, since-base returns, and benchmarks", async () => {
    const html = await renderPage(IndicesPage, {});

    expect(html).toContain("Indices · 指数对比");
    expect(html).toContain("AS-100");
    expect(html).toContain("AS-MC100");

    // AS-100 latest level 1055.503888 → "1,055.5"; return +5.6% (one decimal).
    expect(html).toContain("1,055.5");
    expect(html).toContain("+5.6%");
    // Benchmark proxies appear with their stated names (& is escaped in SSR HTML).
    expect(html).toContain("SPY — S&amp;P 500 (ETF proxy)");
    expect(html).toContain("IWM — Russell 2000 (ETF proxy)");
    expect(html).toContain("IWC — Russell Microcap (ETF proxy)");
    expect(html).toContain("QQQ — Nasdaq-100 (ETF proxy)");
    expect(html).toContain("RSP — S&amp;P 500 Equal Weight (ETF proxy)");
  });

  it("footnotes the PSTG exclusion as a documented gap, not an error", async () => {
    const html = await renderPage(IndicesPage, {});
    expect(html).toContain("PSTG is excluded from AS-MC100");
    expect(html).toContain("pending the monthly review (not an error)");
  });

  it("shows the demo-data notice and the benchmark proxy mapping", async () => {
    const html = await renderPage(IndicesPage, {});
    expect(html).toContain("Demo data");
    // The proxy mapping is stated, never implied.
    expect(html).toContain("SPY (S&amp;P 500)");
    expect(html).toContain("RSP (S&amp;P 500 Equal Weight)");
  });

  it("renders a sparkline for each index", async () => {
    const html = await renderPage(IndicesPage, {});
    expect((html.match(/aria-label="AS-\S+ daily levels since /g) ?? []).length).toBe(2);
  });
});

describe("pure helpers", () => {
  it("sinceBaseReturn computes the first→last percent move", () => {
    expect(sinceBaseReturn({ "2026-11-06": 1000, "2026-11-09": 1055.503888 })).toBeCloseTo(
      5.5503888,
      6,
    );
    expect(sinceBaseReturn({ "2026-11-09": 1000 })).toBe(0);
    expect(sinceBaseReturn({})).toBeNull();
  });

  it("formatLevel renders one decimal with thousands separators", () => {
    expect(formatLevel(1055.503888)).toBe("1,055.5");
    expect(formatLevel(1000)).toBe("1,000.0");
    expect(formatLevel(NaN)).toBe("—");
  });

  it("formatReturnPct renders signed one-decimal percents", () => {
    expect(formatReturnPct(5.5503888)).toBe("+5.6%");
    expect(formatReturnPct(-0.8333333)).toBe("−0.8%");
    expect(formatReturnPct(0)).toBe("0.0%");
    expect(formatReturnPct(null)).toBe("—");
  });

  it("sparklinePoints maps dates left→right and inverts y", () => {
    const points = sparklinePoints(
      { "2026-11-06": 1000, "2026-11-09": 1100, "2026-11-10": 1050 },
      100,
      40,
      0,
    );
    const [first, middle, last] = points.split(" ");
    expect(first).toBe("0.0,40.0"); // base = lowest value → bottom
    expect(middle).toBe("50.0,0.0"); // highest value → top
    expect(last).toBe("100.0,20.0");
  });

  it("sparklinePoints handles flat and single-point series without NaN", () => {
    expect(sparklinePoints({ "2026-11-06": 1000, "2026-11-09": 1000 }, 100, 40, 0)).toBe(
      "0.0,20.0 100.0,20.0",
    );
    expect(sparklinePoints({ "2026-11-06": 1000 }, 100, 40, 0)).toBe("50.0,20.0");
    expect(sparklinePoints({}, 100, 40, 0)).toBe("");
  });

  it("levelDates sorts ISO date keys ascending", () => {
    expect(
      levelDates({ "2026-11-09": 1, "2026-11-06": 2, "2026-11-10": 3 }),
    ).toEqual(["2026-11-06", "2026-11-09", "2026-11-10"]);
  });
});

describe("parseIndexFixture", () => {
  it("parses the committed fixture into typed views", () => {
    const parsed = parseIndexFixture(indexFixture);
    expect(parsed.indices.map((i) => i.indexId)).toEqual(["AS-100", "AS-MC100"]);
    const mc = parsed.indices.find((i) => i.indexId === "AS-MC100");
    expect(mc?.baseDate).toBe("2026-11-09");
    expect(mc?.excluded.map((e) => e.ticker)).toEqual(["PSTG"]);
    expect(mc?.benchmarks.map((b) => b.proxy)).toEqual(["QQQ", "RSP", "SPY"]);
  });

  it("fails loudly on a malformed fixture", () => {
    expect(() => parseIndexFixture(null)).toThrow("malformed");
    expect(() => parseIndexFixture({})).toThrow("malformed");
    expect(() =>
      parseIndexFixture({
        methodology_version: "3.0",
        source: "fixture",
        benchmark_proxy_mapping: {},
        indices: { "AS-100": { base_date: "nope", constituent_count: 100 } },
      }),
    ).toThrow("malformed");
  });
});
