/**
 * Portfolio Monitor — pure logic (design doc v2.1, §7.2 Portfolio Monitor).
 *
 * Three concerns, all deterministic and side-effect free:
 *  1. Input parsing — manual entry and CSV import (all-or-nothing).
 *  2. Latest per-ticker signal / alert selection from published data.
 *  3. The ±50bp rate-stress calculation (spec: tier coefficients, weighted
 *     aggregation — the MVP stance, not a regression beta).
 *
 * Everything here returns data, never prose: the UI localizes error codes
 * from the message dictionary, so the same parse works in EN and zh.
 */
import type { PublicAlert, PublicSignal, Sensitivity } from "./types";

/** One holding row — mirrors db/migrations/0004_user_tables.sql user_portfolios. */
export interface PortfolioHolding {
  ticker: string;
  shares: number;
  costBasis: number | null;
}

// ---------------------------------------------------------------------------
// Manual entry
// ---------------------------------------------------------------------------

export type HoldingField = "ticker" | "shares" | "cost_basis";

export type HoldingParseResult =
  | { ok: true; holding: PortfolioHolding }
  | { ok: false; field: HoldingField };

/** Tickers are 1–6 uppercase ASCII letters; the form accepts any case. */
export function normalizeTicker(raw: string): string {
  return raw.trim().toUpperCase();
}

function isValidTicker(ticker: string): boolean {
  return /^[A-Z]{1,6}$/.test(ticker);
}

function parseNonNegativeNumber(raw: string): number | null {
  if (!/^\d+(\.\d+)?$/.test(raw.trim())) return null;
  const value = Number.parseFloat(raw);
  return Number.isFinite(value) ? value : null;
}

/**
 * Manual-entry parse. Shares must be a positive number; cost basis is
 * optional (empty string) but must be a non-negative number when present.
 */
export function parseHoldingInput(input: {
  ticker: string;
  shares: string;
  costBasis: string;
}): HoldingParseResult {
  const ticker = normalizeTicker(input.ticker);
  if (!isValidTicker(ticker)) return { ok: false, field: "ticker" };

  const shares = parseNonNegativeNumber(input.shares);
  if (shares === null || shares <= 0) return { ok: false, field: "shares" };

  let costBasis: number | null = null;
  if (input.costBasis.trim() !== "") {
    costBasis = parseNonNegativeNumber(input.costBasis);
    if (costBasis === null) return { ok: false, field: "cost_basis" };
  }

  return { ok: true, holding: { ticker, shares, costBasis } };
}

// ---------------------------------------------------------------------------
// CSV import — all-or-nothing
// ---------------------------------------------------------------------------

/** The header the UI documents; exact match after whitespace trimming. */
export const PORTFOLIO_CSV_HEADER = "ticker,shares,cost_basis";

export type CsvErrorField = HoldingField | "columns" | "header";

export interface CsvRowError {
  /** 1-based line number in the file (line 1 is the header). */
  line: number;
  field: CsvErrorField;
}

export type CsvParseResult =
  | { ok: true; holdings: PortfolioHolding[] }
  | { ok: false; errors: CsvRowError[] };

function splitCsvLine(line: string): string[] {
  return line.split(",").map((cell) => cell.trim());
}

/**
 * Whole-file CSV import parse. Every line is validated; if ANY line is
 * malformed the file is rejected in full with every error listed — there is
 * no partial import. Duplicate tickers are allowed: the last row wins, so a
 * user can correct an earlier line within the same file.
 */
export function parsePortfolioCsv(text: string): CsvParseResult {
  const lines = text.split(/\r?\n/).filter((line) => line.trim() !== "");
  if (lines.length === 0) {
    return { ok: false, errors: [{ line: 1, field: "header" }] };
  }

  if (splitCsvLine(lines[0]).join(",").toLowerCase() !== PORTFOLIO_CSV_HEADER) {
    return { ok: false, errors: [{ line: 1, field: "header" }] };
  }

  const errors: CsvRowError[] = [];
  const holdings = new Map<string, PortfolioHolding>();

  lines.slice(1).forEach((line, index) => {
    const lineNo = index + 2;
    const cells = splitCsvLine(line);
    if (cells.length !== 3) {
      errors.push({ line: lineNo, field: "columns" });
      return;
    }
    const [rawTicker, rawShares, rawCost] = cells;

    const ticker = normalizeTicker(rawTicker);
    if (!isValidTicker(ticker)) {
      errors.push({ line: lineNo, field: "ticker" });
      return;
    }
    const shares = parseNonNegativeNumber(rawShares);
    if (shares === null || shares <= 0) {
      errors.push({ line: lineNo, field: "shares" });
      return;
    }
    let costBasis: number | null = null;
    if (rawCost !== "") {
      costBasis = parseNonNegativeNumber(rawCost);
      if (costBasis === null) {
        errors.push({ line: lineNo, field: "cost_basis" });
        return;
      }
    }

    holdings.set(ticker, { ticker, shares, costBasis });
  });

  if (errors.length > 0) return { ok: false, errors };
  return { ok: true, holdings: [...holdings.values()] };
}

// ---------------------------------------------------------------------------
// Latest per-ticker data
// ---------------------------------------------------------------------------

/** Newest published signal that mentions the ticker, or null. */
export function latestSignalForTicker(
  signals: PublicSignal[],
  ticker: string,
): PublicSignal | null {
  return (
    signals
      .filter((signal) => signal.tickers.includes(ticker))
      .sort((a, b) => (a.publishedAt < b.publishedAt ? 1 : -1))[0] ?? null
  );
}

/** Newest APPROVED alert for the ticker (pending rows are not published). */
export function latestApprovedAlertForTicker(
  alerts: PublicAlert[],
  ticker: string,
): PublicAlert | null {
  return (
    alerts
      .filter((alert) => alert.ticker === ticker && alert.approved)
      .sort((a, b) => (a.alertDate < b.alertDate ? 1 : -1))[0] ?? null
  );
}

/** One rendered holding row: holding + latest published data + stress columns. */
export interface PortfolioRow {
  holding: PortfolioHolding;
  signal: PublicSignal | null;
  alphaScore: number | null;
  scoreDelta: number | null;
  rateSensitivity: Sensitivity | null;
  /** Per-position impact at +50bp; null when the position is unrated. */
  up50ImpactPct: number | null;
  /** Per-position impact at −50bp; null when the position is unrated. */
  down50ImpactPct: number | null;
}

/**
 * Join holdings with published data. Sensitivity and stress come from the
 * latest signal's macro_overlay; Alpha Score from the latest APPROVED alert.
 * Unrated positions render "—", never an invented number.
 */
export function buildPortfolioRows(
  holdings: PortfolioHolding[],
  signals: PublicSignal[],
  alerts: PublicAlert[],
): PortfolioRow[] {
  return holdings.map((holding) => {
    const signal = latestSignalForTicker(signals, holding.ticker);
    const alert = latestApprovedAlertForTicker(alerts, holding.ticker);
    const rateSensitivity = signal?.macroOverlay.rateSensitivity ?? null;
    return {
      holding,
      signal,
      alphaScore: alert?.alphaScore ?? null,
      scoreDelta: alert?.scoreDelta ?? null,
      rateSensitivity,
      up50ImpactPct:
        rateSensitivity !== null
          ? rateStressImpactPct(rateSensitivity, RATE_STRESS_BASIS_POINTS)
          : null,
      down50ImpactPct:
        rateSensitivity !== null
          ? rateStressImpactPct(rateSensitivity, -RATE_STRESS_BASIS_POINTS)
          : null,
    };
  });
}

// ---------------------------------------------------------------------------
// ±50bp rate stress — deterministic tier coefficients (spec: MVP stance)
// ---------------------------------------------------------------------------

/** The shock the UI presents: a ±50 basis point policy-rate move. */
export const RATE_STRESS_BASIS_POINTS = 50;

/**
 * Estimated price impact (%) at exactly a 50bp rate move, per rate
 * -sensitivity tier from the latest signal's macro_overlay. Tier
 * coefficients, NOT regression betas — the spec's MVP stance; the
 * regression version belongs to the Factors module (Appendix B).
 */
const TIER_IMPACT_PCT_AT_50BP: Record<Sensitivity, number> = {
  high: 1.5,
  medium: 0.75,
  low: 0.3,
};

/**
 * Direction of effect: RISING rates compress valuations, so a +50bp shock
 * is a NEGATIVE impact for every tier; a −50bp shock is symmetric positive.
 * Deterministic: identical inputs always produce identical outputs.
 */
export function rateStressImpactPct(
  sensitivity: Sensitivity,
  shockBasisPoints: number,
): number {
  return (
    -(shockBasisPoints / RATE_STRESS_BASIS_POINTS) *
    TIER_IMPACT_PCT_AT_50BP[sensitivity]
  );
}

export interface StressPositionInput {
  shares: number;
  /** From the latest signal's macro_overlay; null = unrated. */
  rateSensitivity: Sensitivity | null;
}

export interface PortfolioStress {
  /** Portfolio-level impact for a +50bp shock (negative %). */
  up50ImpactPct: number;
  /** Portfolio-level impact for a −50bp shock (positive %). */
  down50ImpactPct: number;
  /** Shares covered by a rated sensitivity — they drive the weighted mean. */
  ratedShares: number;
  /** Total shares held; ratedShares ≤ totalShares, the gap is unrated. */
  totalShares: number;
}

/**
 * Share-weighted aggregation of per-position impacts. Positions whose
 * latest signal carries no rate sensitivity are excluded from the weighted
 * mean (not silently treated as insensitive) and reported via ratedShares.
 * With no rated shares the impact is 0 with zero coverage — never invented.
 */
export function portfolioStress(positions: StressPositionInput[]): PortfolioStress {
  const totalShares = positions.reduce((sum, p) => sum + p.shares, 0);
  const rated = positions.filter(
    (p): p is StressPositionInput & { rateSensitivity: Sensitivity } =>
      p.rateSensitivity !== null,
  );
  const ratedShares = rated.reduce((sum, p) => sum + p.shares, 0);

  if (ratedShares === 0) {
    return { up50ImpactPct: 0, down50ImpactPct: 0, ratedShares: 0, totalShares };
  }

  const weighted = (shock: number): number =>
    rated.reduce(
      (sum, p) =>
        sum +
        (p.shares / ratedShares) * rateStressImpactPct(p.rateSensitivity, shock),
      0,
    );

  return {
    up50ImpactPct: weighted(RATE_STRESS_BASIS_POINTS),
    down50ImpactPct: weighted(-RATE_STRESS_BASIS_POINTS),
    ratedShares,
    totalShares,
  };
}
