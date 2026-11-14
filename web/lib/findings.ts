/**
 * Reader for the curated market-findings fixture (`web/fixtures/market-findings.json`).
 *
 * Findings are editorial research content, sourced and dated — not a store
 * query — so they are served from the fixture in both data modes, exactly like
 * methodology content. The fixture is never trusted: every period is validated
 * eagerly and a malformed row throws (same loud-failure convention as
 * `parseRows`), so a broken fixture fails the page instead of rendering a
 * silent half-truth.
 */
import marketFindingsFixture from "@/fixtures/market-findings.json";
import { THEMES } from "./themes";

export type Quarter = 1 | 2 | 3 | 4;

export interface MarketFinding {
  themeId: string;
  headlineEn: string;
  headlineZh: string;
  detailEn: string;
  detailZh: string;
  /** Absolute https URL — source integrity is validated at parse time. */
  sourceUrl: string;
  /** e.g. "company disclosure · 2026-04" */
  sourceLabel: string;
}

export interface PeriodFindings {
  year: number;
  /** null = the full-year (year-to-date) entry. */
  quarter: Quarter | null;
  titleEn: string;
  titleZh: string;
  summaryEn: string;
  summaryZh: string;
  findings: MarketFinding[];
}

export class FindingsParseError extends Error {
  constructor(message: string) {
    super(`market-findings fixture is malformed: ${message}`);
    this.name = "FindingsParseError";
  }
}

const THEME_IDS = new Set(THEMES.map((t) => t.id));
const QUARTERS: readonly Quarter[] = [1, 2, 3, 4];

function requireString(record: Record<string, unknown>, key: string, where: string): string {
  const value = record[key];
  if (typeof value !== "string" || value.length === 0) {
    throw new FindingsParseError(`${where}.${key} must be a non-empty string`);
  }
  return value;
}

function parseFinding(raw: unknown, where: string): MarketFinding {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    throw new FindingsParseError(`${where} must be an object`);
  }
  const record = raw as Record<string, unknown>;
  const themeId = requireString(record, "theme_id", where);
  if (!THEME_IDS.has(themeId)) {
    throw new FindingsParseError(`${where}.theme_id "${themeId}" is not a known heat-card theme`);
  }
  const sourceUrl = requireString(record, "source_url", where);
  if (!/^https:\/\/\S+$/.test(sourceUrl)) {
    throw new FindingsParseError(`${where}.source_url must be an absolute https URL`);
  }
  return {
    themeId,
    headlineEn: requireString(record, "headline_en", where),
    headlineZh: requireString(record, "headline_zh", where),
    detailEn: requireString(record, "detail_en", where),
    detailZh: requireString(record, "detail_zh", where),
    sourceUrl,
    sourceLabel: requireString(record, "source_label", where),
  };
}

function parsePeriod(raw: unknown, index: number): PeriodFindings {
  const where = `periods[${index}]`;
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    throw new FindingsParseError(`${where} must be an object`);
  }
  const record = raw as Record<string, unknown>;
  const year = record.year;
  if (typeof year !== "number" || !Number.isInteger(year)) {
    throw new FindingsParseError(`${where}.year must be an integer`);
  }
  const quarterRaw = record.quarter;
  const quarter =
    quarterRaw === null || quarterRaw === undefined
      ? null
      : QUARTERS.includes(quarterRaw as Quarter)
        ? (quarterRaw as Quarter)
        : null;
  if (quarterRaw !== null && quarterRaw !== undefined && quarter === null) {
    throw new FindingsParseError(`${where}.quarter must be 1–4 or null`);
  }
  const findingsRaw = record.findings;
  if (!Array.isArray(findingsRaw)) {
    throw new FindingsParseError(`${where}.findings must be an array`);
  }
  const place = `${where} (year ${year}${quarter ? ` Q${quarter}` : ""})`;
  return {
    year,
    quarter,
    titleEn: requireString(record, "title_en", place),
    titleZh: requireString(record, "title_zh", place),
    summaryEn: requireString(record, "summary_en", place),
    summaryZh: requireString(record, "summary_zh", place),
    findings: findingsRaw.map((finding, i) => parseFinding(finding, `${place}.findings[${i}]`)),
  };
}

/** Validate a raw findings payload; throws on any malformed period or finding. */
export function parseMarketFindings(raw: unknown): PeriodFindings[] {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    throw new FindingsParseError("payload must be an object");
  }
  const record = raw as Record<string, unknown>;
  if (typeof record.generated_at !== "string" || record.generated_at.length === 0) {
    throw new FindingsParseError("generated_at must be a non-empty string");
  }
  if (!Array.isArray(record.periods)) {
    throw new FindingsParseError("periods must be an array");
  }
  const periods = record.periods.map(parsePeriod);
  const seen = new Set<string>();
  for (const period of periods) {
    const key = `${period.year}:${period.quarter ?? "Y"}`;
    if (seen.has(key)) {
      throw new FindingsParseError(`duplicate period ${key}`);
    }
    seen.add(key);
  }
  return periods;
}

const PERIODS: PeriodFindings[] = parseMarketFindings(marketFindingsFixture);

/** Years present in the fixture, newest first. */
export function getAvailableYears(): number[] {
  return [...new Set(PERIODS.map((p) => p.year))].sort((a, b) => b - a);
}

/** Quarters with a curated entry for the given year, ascending. */
export function getAvailableQuarters(year: number): Quarter[] {
  return QUARTERS.filter((q) => periodsFor(year, q).length > 0);
}

/** The full-year (year-to-date) entry; null when the year has none. */
export function getYearlyFindings(year: number): PeriodFindings | null {
  return PERIODS.find((p) => p.year === year && p.quarter === null) ?? null;
}

/** One quarter's entry; null when that quarter was never curated. */
export function getQuarterlyFindings(year: number, quarter: Quarter): PeriodFindings | null {
  return periodsFor(year, quarter)[0] ?? null;
}

/** The most recent curated quarter across all years — the picker's default. */
export function getMostRecentQuarter(): { year: number; quarter: Quarter } | null {
  const quarterly = PERIODS.filter((p) => p.quarter !== null);
  if (quarterly.length === 0) return null;
  const latest = quarterly.reduce((a, b) =>
    a.year > b.year || (a.year === b.year && (a.quarter ?? 0) > (b.quarter ?? 0)) ? a : b,
  );
  return { year: latest.year, quarter: latest.quarter as Quarter };
}

function periodsFor(year: number, quarter: Quarter): PeriodFindings[] {
  return PERIODS.filter((p) => p.year === year && p.quarter === quarter);
}
