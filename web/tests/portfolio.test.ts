/**
 * Portfolio pure-logic tests: CSV parsing (all-or-nothing), manual entry,
 * latest signal/alert selection, and the deterministic ±50bp rate stress.
 */
import { describe, expect, it } from "vitest";

import signalsFixture from "../fixtures/signals.json";
import alertsFixture from "../fixtures/alert-history.json";
import {
  buildPortfolioRows,
  latestApprovedAlertForTicker,
  latestSignalForTicker,
  normalizeTicker,
  parseHoldingInput,
  parsePortfolioCsv,
  portfolioStress,
  PORTFOLIO_CSV_HEADER,
  RATE_STRESS_BASIS_POINTS,
  rateStressImpactPct,
} from "../lib/portfolio";
import { toPublicAlert, toPublicSignal } from "../lib/view";

const signals = (signalsFixture as unknown[]).map(toPublicSignal);
const alerts = (alertsFixture as unknown[]).map(toPublicAlert);

describe("manual entry parse", () => {
  it("parses a valid holding, uppercasing the ticker", () => {
    expect(parseHoldingInput({ ticker: " vst ", shares: "100", costBasis: "" })).toEqual({
      ok: true,
      holding: { ticker: "VST", shares: 100, costBasis: null },
    });
  });

  it("accepts fractional shares and a cost basis", () => {
    expect(parseHoldingInput({ ticker: "nee", shares: "12.5", costBasis: "64.20" })).toEqual({
      ok: true,
      holding: { ticker: "NEE", shares: 12.5, costBasis: 64.2 },
    });
  });

  it("rejects bad tickers, non-positive shares, and negative costs", () => {
    expect(parseHoldingInput({ ticker: "TOOLONGTICKER", shares: "1", costBasis: "" })).toEqual({
      ok: false,
      field: "ticker",
    });
    expect(parseHoldingInput({ ticker: "VST", shares: "0", costBasis: "" })).toEqual({
      ok: false,
      field: "shares",
    });
    expect(parseHoldingInput({ ticker: "VST", shares: "-5", costBasis: "" })).toEqual({
      ok: false,
      field: "shares",
    });
    expect(parseHoldingInput({ ticker: "VST", shares: "1", costBasis: "-1" })).toEqual({
      ok: false,
      field: "cost_basis",
    });
    expect(parseHoldingInput({ ticker: "VST", shares: "abc", costBasis: "" })).toEqual({
      ok: false,
      field: "shares",
    });
  });

  it("normalizes tickers", () => {
    expect(normalizeTicker(" vst ")).toBe("VST");
  });
});

describe("CSV import — all-or-nothing", () => {
  it("parses a valid file, skipping blank lines", () => {
    const csv = `${PORTFOLIO_CSV_HEADER}\nVST,100,25\n\nnee,50,\n`;
    expect(parsePortfolioCsv(csv)).toEqual({
      ok: true,
      holdings: [
        { ticker: "VST", shares: 100, costBasis: 25 },
        { ticker: "NEE", shares: 50, costBasis: null },
      ],
    });
  });

  it("rejects a wrong header outright", () => {
    const result = parsePortfolioCsv("ticker,shares\nVST,100\n");
    expect(result).toEqual({ ok: false, errors: [{ line: 1, field: "header" }] });
  });

  it("rejects the WHOLE file when any row is malformed — no partial import", () => {
    const csv = `${PORTFOLIO_CSV_HEADER}\nVST,100,\nNOT A VALID ROW\nNEE,50,\n`;
    const result = parsePortfolioCsv(csv);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.errors).toContainEqual({ line: 3, field: "columns" });
    }
  });

  it("lists every offending line with its field", () => {
    const csv = `${PORTFOLIO_CSV_HEADER}\nTOOLONGTICKER,100,\nX,50,\nNEE,-5,\nNEE,10,notanumber\n`;
    const result = parsePortfolioCsv(csv);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.errors).toEqual([
        { line: 2, field: "ticker" },
        { line: 4, field: "shares" },
        { line: 5, field: "cost_basis" },
      ]);
    }
  });

  it("keeps the last row for duplicate tickers", () => {
    const csv = `${PORTFOLIO_CSV_HEADER}\nVST,100,\nVST,200,30\n`;
    expect(parsePortfolioCsv(csv)).toEqual({
      ok: true,
      holdings: [{ ticker: "VST", shares: 200, costBasis: 30 }],
    });
  });

  it("rejects an empty file", () => {
    expect(parsePortfolioCsv("")).toEqual({ ok: false, errors: [{ line: 1, field: "header" }] });
  });
});

describe("latest per-ticker data", () => {
  it("picks the newest signal mentioning the ticker", () => {
    const latest = latestSignalForTicker(signals, "DLR");
    expect(latest?.publishedAt.slice(0, 10)).toBe("2026-11-14");
  });

  it("returns null for a ticker with no signals", () => {
    expect(latestSignalForTicker(signals, "ZZZZ")).toBeNull();
  });

  it("picks the newest APPROVED alert, skipping pending rows", () => {
    // SYM's only alert row is pending approval in the fixture — not published.
    expect(latestApprovedAlertForTicker(alerts, "SYM")).toBeNull();
    const vst = latestApprovedAlertForTicker(alerts, "VST");
    expect(vst?.alphaScore).toBe(72);
  });
});

describe("±50bp rate stress — deterministic", () => {
  it("maps tiers to negative impacts when rates RISE (+50bp)", () => {
    expect(rateStressImpactPct("high", 50)).toBeCloseTo(-1.5);
    expect(rateStressImpactPct("medium", 50)).toBeCloseTo(-0.75);
    expect(rateStressImpactPct("low", 50)).toBeCloseTo(-0.3);
  });

  it("maps tiers to symmetric positive impacts when rates FALL (−50bp)", () => {
    expect(rateStressImpactPct("high", -50)).toBeCloseTo(1.5);
    expect(rateStressImpactPct("medium", -50)).toBeCloseTo(0.75);
    expect(rateStressImpactPct("low", -50)).toBeCloseTo(0.3);
  });

  it("is deterministic: identical inputs always produce identical outputs", () => {
    const positions = [
      { shares: 100, rateSensitivity: "high" as const },
      { shares: 50, rateSensitivity: "medium" as const },
      { shares: 25, rateSensitivity: null },
    ];
    const a = portfolioStress(positions);
    for (let i = 0; i < 5; i += 1) {
      expect(portfolioStress(positions)).toEqual(a);
    }
    // And the per-position calculation too, both directions.
    for (let i = 0; i < 5; i += 1) {
      expect(rateStressImpactPct("high", RATE_STRESS_BASIS_POINTS)).toBe(
        rateStressImpactPct("high", RATE_STRESS_BASIS_POINTS),
      );
      expect(rateStressImpactPct("low", -RATE_STRESS_BASIS_POINTS)).toBe(
        rateStressImpactPct("low", -RATE_STRESS_BASIS_POINTS),
      );
    }
  });

  it("share-weights the portfolio mean and reports coverage honestly", () => {
    const stress = portfolioStress([
      { shares: 100, rateSensitivity: "high" },
      { shares: 100, rateSensitivity: "low" },
      { shares: 100, rateSensitivity: null },
    ]);
    // Rated mean: (−1.5 + −0.3) / 2 — unrated 100 shares excluded, disclosed.
    expect(stress.up50ImpactPct).toBeCloseTo(-0.9);
    expect(stress.down50ImpactPct).toBeCloseTo(0.9);
    expect(stress.ratedShares).toBe(200);
    expect(stress.totalShares).toBe(300);
  });

  it("returns zero impact with zero coverage when nothing is rated", () => {
    const stress = portfolioStress([{ shares: 10, rateSensitivity: null }]);
    expect(stress).toEqual({ up50ImpactPct: 0, down50ImpactPct: 0, ratedShares: 0, totalShares: 10 });
  });

  it("handles an empty portfolio", () => {
    const stress = portfolioStress([]);
    expect(stress).toEqual({ up50ImpactPct: 0, down50ImpactPct: 0, ratedShares: 0, totalShares: 0 });
  });
});

describe("portfolio rows join", () => {
  it("joins holdings with the latest signal and approved alert", () => {
    const rows = buildPortfolioRows(
      [
        { ticker: "VST", shares: 100, costBasis: 25 },
        { ticker: "NOTCOVERED", shares: 5, costBasis: null },
      ],
      signals,
      alerts,
    );
    expect(rows).toHaveLength(2);

    const vst = rows[0];
    expect(vst.rateSensitivity).toBe("high");
    expect(vst.alphaScore).toBe(72);
    expect(vst.scoreDelta).toBe(3.5);
    expect(vst.up50ImpactPct).toBeCloseTo(-1.5);
    expect(vst.down50ImpactPct).toBeCloseTo(1.5);

    // A holding outside coverage renders "—", never an invented number.
    const uncovered = rows[1];
    expect(uncovered.signal).toBeNull();
    expect(uncovered.rateSensitivity).toBeNull();
    expect(uncovered.up50ImpactPct).toBeNull();
    expect(uncovered.alphaScore).toBeNull();
  });
});
