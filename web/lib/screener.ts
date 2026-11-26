/**
 * Screener (V2.1 §7.2) — pure filter + row-building logic.
 *
 * One row per covered company, enriched with the company's latest published
 * signal (direction / strength / rate sensitivity come from that signal) and
 * its latest approved weekly alert (Alpha Score + weekly delta). The same
 * `ScreenerFilters` shape drives the page, the CSV export route, and the
 * tests, so the table and the exported file can never disagree.
 */
import { formatDelta } from "./format";
import type { Messages } from "./messages";
import { themeLabel } from "./themes";
import type { PublicAlert, PublicCompany, PublicSignal, Sensitivity } from "./types";

export interface ScreenerFilters {
  theme?: string;
  category?: string;
  /** Inclusive strength range over the latest signal (1–5). */
  minStrength?: number;
  maxStrength?: number;
  /** Inclusive Alpha Score range (0–100). Rows without a score never match. */
  minScore?: number;
  maxScore?: number;
  /** Rate-sensitivity tier of the latest signal (macro_overlay.rate_sensitivity). */
  sensitivity?: Sensitivity;
}

export interface ScreenerRow {
  ticker: string;
  name: string;
  category: string;
  themes: string[];
  latestSignal: PublicSignal | null;
  alphaScore: number | null;
  scoreDelta: number | null;
  rateSensitivity: Sensitivity | null;
}

/** Page search params (Next.js record shape) or a route handler's URLSearchParams. */
export type ScreenerParamsInput = Record<string, string | string[] | undefined> | URLSearchParams;

function firstValue(params: ScreenerParamsInput, key: string): string | undefined {
  if (params instanceof URLSearchParams) return params.get(key) ?? undefined;
  const value = params[key];
  return Array.isArray(value) ? value[0] : value;
}

/** Tolerant integer parse within [lo, hi]; anything else is unset. */
function boundedInt(raw: string | undefined, lo: number, hi: number): number | undefined {
  const parsed = Number.parseInt(raw ?? "", 10);
  return parsed >= lo && parsed <= hi ? parsed : undefined;
}

/** Tolerant numeric parse within [lo, hi]; empty or malformed values are unset. Number("") is 0, so the empty check must come first. */
function boundedNumber(raw: string | undefined, lo: number, hi: number): number | undefined {
  if (!raw || raw.trim() === "") return undefined;
  const parsed = Number(raw);
  return Number.isFinite(parsed) && parsed >= lo && parsed <= hi ? parsed : undefined;
}

const SENSITIVITIES: Sensitivity[] = ["high", "medium", "low"];

/** Unknown or malformed filter values are treated as unset, never as errors. */
export function parseScreenerFilters(params: ScreenerParamsInput): ScreenerFilters {
  const sensitivity = firstValue(params, "sensitivity");
  return {
    theme: firstValue(params, "theme") || undefined,
    category: firstValue(params, "category") || undefined,
    minStrength: boundedInt(firstValue(params, "minStrength"), 1, 5),
    maxStrength: boundedInt(firstValue(params, "maxStrength"), 1, 5),
    minScore: boundedNumber(firstValue(params, "minScore"), 0, 100),
    maxScore: boundedNumber(firstValue(params, "maxScore"), 0, 100),
    sensitivity: SENSITIVITIES.includes(sensitivity as Sensitivity)
      ? (sensitivity as Sensitivity)
      : undefined,
  };
}

export function hasActiveScreenerFilters(filters: ScreenerFilters): boolean {
  return (
    filters.theme !== undefined ||
    filters.category !== undefined ||
    filters.minStrength !== undefined ||
    filters.maxStrength !== undefined ||
    filters.minScore !== undefined ||
    filters.maxScore !== undefined ||
    filters.sensitivity !== undefined
  );
}

/**
 * One row per covered company. The latest signal is the newest by published
 * time (signal id breaks ties); only approved alerts contribute scores.
 */
export function buildScreenerRows(
  companies: PublicCompany[],
  signals: PublicSignal[],
  alerts: PublicAlert[],
): ScreenerRow[] {
  const latestSignalByTicker = new Map<string, PublicSignal>();
  for (const signal of signals) {
    for (const ticker of signal.tickers) {
      const current = latestSignalByTicker.get(ticker);
      if (!current || isNewer(signal, current)) {
        latestSignalByTicker.set(ticker, signal);
      }
    }
  }

  const latestAlertByTicker = new Map<string, PublicAlert>();
  for (const alert of alerts) {
    if (!alert.approved) continue;
    const current = latestAlertByTicker.get(alert.ticker);
    if (!current || alert.alertDate > current.alertDate) {
      latestAlertByTicker.set(alert.ticker, alert);
    }
  }

  return companies.map((company) => {
    const latestSignal = latestSignalByTicker.get(company.ticker) ?? null;
    const latestAlert = latestAlertByTicker.get(company.ticker) ?? null;
    return {
      ticker: company.ticker,
      name: company.name,
      category: company.category,
      themes: company.themes,
      latestSignal,
      alphaScore: latestAlert?.alphaScore ?? null,
      scoreDelta: latestAlert?.scoreDelta ?? null,
      rateSensitivity: latestSignal?.macroOverlay.rateSensitivity ?? null,
    };
  });
}

function isNewer(candidate: PublicSignal, incumbent: PublicSignal): boolean {
  const candidateMs = Date.parse(candidate.publishedAt);
  const incumbentMs = Date.parse(incumbent.publishedAt);
  if (candidateMs !== incumbentMs) return candidateMs > incumbentMs;
  return candidate.signalId > incumbent.signalId;
}

/** In-memory application of the five spec'd condition types. */
export function applyScreenerFilters(
  rows: ScreenerRow[],
  filters: ScreenerFilters,
): ScreenerRow[] {
  return rows.filter((row) => {
    if (filters.theme && !row.themes.includes(filters.theme)) return false;
    if (
      filters.category &&
      row.latestSignal?.category !== filters.category
    ) {
      return false;
    }
    if (
      filters.minStrength !== undefined &&
      (row.latestSignal?.strength ?? -Infinity) < filters.minStrength
    ) {
      return false;
    }
    if (
      filters.maxStrength !== undefined &&
      (row.latestSignal?.strength ?? -Infinity) > filters.maxStrength
    ) {
      return false;
    }
    if (
      filters.sensitivity !== undefined &&
      row.rateSensitivity !== filters.sensitivity
    ) {
      return false;
    }
    if (
      (filters.minScore !== undefined || filters.maxScore !== undefined) &&
      row.alphaScore === null
    ) {
      return false;
    }
    if (filters.minScore !== undefined && (row.alphaScore ?? 0) < filters.minScore) {
      return false;
    }
    if (filters.maxScore !== undefined && (row.alphaScore ?? 0) > filters.maxScore) {
      return false;
    }
    return true;
  });
}

/** Result order: highest Alpha Score first, unscored names last, ticker breaks ties. */
export function sortScreenerRows(rows: ScreenerRow[]): ScreenerRow[] {
  return [...rows].sort((a, b) => {
    if (a.alphaScore !== b.alphaScore) return (b.alphaScore ?? -1) - (a.alphaScore ?? -1);
    return a.ticker > b.ticker ? 1 : -1;
  });
}

/** Query string carrying the current filter set — the page's export link and the export route agree on it. */
export function screenerFilterQuery(filters: ScreenerFilters): string {
  const params = new URLSearchParams();
  if (filters.theme) params.set("theme", filters.theme);
  if (filters.category) params.set("category", filters.category);
  if (filters.minStrength !== undefined) params.set("minStrength", String(filters.minStrength));
  if (filters.maxStrength !== undefined) params.set("maxStrength", String(filters.maxStrength));
  if (filters.minScore !== undefined) params.set("minScore", String(filters.minScore));
  if (filters.maxScore !== undefined) params.set("maxScore", String(filters.maxScore));
  if (filters.sensitivity) params.set("sensitivity", filters.sensitivity);
  return params.toString();
}

const CSV_COLUMNS = [
  "Ticker",
  "Company",
  "Themes",
  "Category",
  "Latest signal",
  "Direction",
  "Strength",
  "Alpha Score",
  "Delta",
  "Rate sensitivity",
] as const;

function csvCell(value: string): string {
  return `"${value.replace(/"/g, '""')}"`;
}

function csvLine(cells: string[]): string {
  return cells.map(csvCell).join(",");
}

/**
 * CSV for the current result set. Labels go through the same dictionary the
 * table uses, so the exported file matches the rendered rows in the active
 * language. RFC 4180: CRLF terminators, all fields quoted.
 */
export function toScreenerCsv(
  rows: ScreenerRow[],
  t: Messages,
  locale: "en" | "zh",
): string {
  const lines = [csvLine([...CSV_COLUMNS])];
  for (const row of rows) {
    lines.push(
      csvLine([
        row.ticker,
        row.name,
        row.themes.map((theme) => themeLabel(theme, locale)).join("; "),
        universeCategoryLabel(row.category, t),
        row.latestSignal?.signalId ?? "",
        row.latestSignal ? t.badges[row.latestSignal.direction] : "",
        row.latestSignal ? String(row.latestSignal.strength) : "",
        row.alphaScore !== null ? String(row.alphaScore) : "",
        row.scoreDelta !== null ? formatDelta(row.scoreDelta) : "",
        row.rateSensitivity ? t.screener.sensitivity[row.rateSensitivity] : "",
      ]),
    );
  }
  return lines.join("\r\n") + "\r\n";
}

function universeCategoryLabel(category: string, t: Messages): string {
  return t.universeCategories[category] ?? category;
}
