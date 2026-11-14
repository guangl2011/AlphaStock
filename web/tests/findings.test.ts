/**
 * Market-findings data layer (web/lib/findings.ts) against the curated
 * fixture — the same loud-failure convention as the signal fixtures: a
 * malformed period, an unknown theme_id, or a missing field must throw,
 * never render a silent half-truth.
 */
import { describe, expect, it } from "vitest";

import findingsFixture from "../fixtures/market-findings.json";
import {
  getAvailableQuarters,
  getAvailableYears,
  getMostRecentQuarter,
  getQuarterlyFindings,
  getYearlyFindings,
  parseMarketFindings,
} from "../lib/findings";

const raw = findingsFixture as unknown;

function mutated(mutate: (payload: Record<string, unknown>) => void): unknown {
  const clone = structuredClone(raw) as Record<string, unknown>;
  mutate(clone);
  return clone;
}

describe("market findings data layer", () => {
  it("parses the shipped fixture and exposes the available periods", () => {
    expect(getAvailableYears()).toEqual([2026]);
    expect(getAvailableQuarters(2026)).toEqual([1, 2, 3]);
    expect(getMostRecentQuarter()).toEqual({ year: 2026, quarter: 3 });
  });

  it("returns the 2026 yearly entry labeled year-to-date in both languages", () => {
    const yearly = getYearlyFindings(2026);
    expect(yearly).not.toBeNull();
    expect(yearly?.quarter).toBeNull();
    expect(yearly?.titleEn).toContain("year to date");
    expect(yearly?.titleZh).toContain("年初至今");
    expect(yearly?.findings.length).toBeGreaterThan(0);
  });

  it("returns quarterly entries for Q1–Q3 2026 with both languages", () => {
    const zhQuarters: Record<number, string> = { 1: "一", 2: "二", 3: "三", 4: "四" };
    for (const quarter of [1, 2, 3] as const) {
      const entry = getQuarterlyFindings(2026, quarter);
      expect(entry?.titleEn).toContain(`Q${quarter}`);
      expect(entry?.titleZh).toContain(`第${zhQuarters[quarter]}季度`);
      expect(entry?.summaryEn.length ?? 0).toBeGreaterThan(0);
      expect(entry?.summaryZh.length ?? 0).toBeGreaterThan(0);
    }
    expect(getQuarterlyFindings(2026, 4)).toBeNull();
  });

  it("returns no yearly entry for an unresearched year", () => {
    expect(getYearlyFindings(2025)).toBeNull();
    expect(getAvailableQuarters(2025)).toEqual([]);
  });

  it("throws on an unknown theme_id", () => {
    const bad = mutated((payload) => {
      const periods = payload.periods as Array<Record<string, unknown>>;
      ((periods[0].findings as Array<Record<string, unknown>>)[0] as Record<string, unknown>)
        .theme_id = "Not A Theme";
    });
    expect(() => parseMarketFindings(bad)).toThrow(/not a known heat-card theme/);
  });

  it("throws on a period missing a required field", () => {
    const bad = mutated((payload) => {
      const periods = payload.periods as Array<Record<string, unknown>>;
      delete periods[1].title_zh;
    });
    expect(() => parseMarketFindings(bad)).toThrow(/title_zh/);
  });

  it("throws on a non-https source_url or an empty source_label", () => {
    const badUrl = mutated((payload) => {
      const periods = payload.periods as Array<Record<string, unknown>>;
      ((periods[0].findings as Array<Record<string, unknown>>)[0] as Record<string, unknown>)
        .source_url = "http://insecure.example/";
    });
    expect(() => parseMarketFindings(badUrl)).toThrow(/absolute https URL/);

    const badLabel = mutated((payload) => {
      const periods = payload.periods as Array<Record<string, unknown>>;
      ((periods[0].findings as Array<Record<string, unknown>>)[0] as Record<string, unknown>)
        .source_label = "";
    });
    expect(() => parseMarketFindings(badLabel)).toThrow(/source_label/);
  });

  it("carries a dated source label on every finding in every period", () => {
    for (const period of parseMarketFindings(raw)) {
      for (const finding of period.findings) {
        expect(finding.sourceUrl).toMatch(/^https:\/\//);
        expect(finding.sourceLabel).toMatch(/\d{4}/);
      }
    }
  });
});
