/**
 * Factors library data for /factors — the Correlation & Factor-Relationship
 * Model (pipelines/pipelines/correlation, docs/correlation-model.md) run in
 * research mode over the 2024-01-02 frame, exported to fixtures/factors.json
 * by `python -m pipelines.correlation.web_fixture`.
 */
import factorsFixture from "@/fixtures/factors.json";
import { ConfigError, isFixtureMode } from "./data";

export interface FactorDef {
  factor: string;
  kind: "return" | "spread";
  proxies: string[];
  construction: string;
  inRegression: boolean;
}

export interface FactorStat {
  beta: number | null;
  stdError: number | null;
  tStat: number | null;
  corrFull: number | null;
  corr63d: number | null;
  corr21d: number | null;
}

export interface IndexExposure {
  rSquared: number | null;
  regressionDays: number;
  droppedCollinear: string;
  factors: Record<string, FactorStat>;
}

export interface Leader {
  ticker: string;
  beta63d: number | null;
  corr63d: number | null;
}

export interface FactorsPageData {
  methodologyVersion: string;
  source: string;
  indexIds: string[];
  series: string[];
  catalog: FactorDef[];
  exposures: Record<string, IndexExposure>;
  /** window → "a|b" → correlation (pairs stored once, a < b in pipeline order). */
  correlations: Record<string, Record<string, number | null>>;
  leaders: Record<string, Record<string, Leader[]>>;
}

type Raw = Record<string, unknown>;

function obj(raw: unknown, label: string): Raw {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    throw new Error(`${label} is malformed`);
  }
  return raw as Raw;
}

function maybeNum(raw: unknown, label: string): number | null {
  if (raw === null) return null;
  if (typeof raw !== "number" || !Number.isFinite(raw)) throw new Error(`${label} is not a number`);
  return raw;
}

function strArray(raw: unknown, label: string): string[] {
  if (!Array.isArray(raw) || raw.some((x) => typeof x !== "string")) {
    throw new Error(`${label} is malformed`);
  }
  return raw as string[];
}

export function parseFactorsFixture(raw: unknown): FactorsPageData {
  const f = obj(raw, "factors fixture");
  if (typeof f.methodology_version !== "string" || typeof f.source !== "string") {
    throw new Error("factors fixture is malformed: missing version/source");
  }
  const catalog = (Array.isArray(f.catalog) ? f.catalog : []).map((c, i) => {
    const r = obj(c, `catalog[${i}]`);
    if (typeof r.factor !== "string" || typeof r.construction !== "string") {
      throw new Error(`catalog[${i}] is malformed`);
    }
    return {
      factor: r.factor,
      kind: r.kind === "spread" ? "spread" : "return",
      proxies: strArray(r.proxies, `catalog[${i}].proxies`),
      construction: r.construction,
      inRegression: r.in_regression === true,
    } satisfies FactorDef;
  });
  const exposures: Record<string, IndexExposure> = {};
  for (const [id, rawExp] of Object.entries(obj(f.exposures, "exposures"))) {
    const e = obj(rawExp, `exposures.${id}`);
    const factors: Record<string, FactorStat> = {};
    for (const [name, rawStat] of Object.entries(obj(e.factors, `exposures.${id}.factors`))) {
      const s = obj(rawStat, `exposures.${id}.${name}`);
      const l = `exposures.${id}.${name}`;
      factors[name] = {
        beta: maybeNum(s.beta, `${l}.beta`),
        stdError: maybeNum(s.std_error, `${l}.std_error`),
        tStat: maybeNum(s.t_stat, `${l}.t_stat`),
        corrFull: maybeNum(s.corr_full, `${l}.corr_full`),
        corr63d: maybeNum(s.corr_63d, `${l}.corr_63d`),
        corr21d: maybeNum(s.corr_21d, `${l}.corr_21d`),
      };
    }
    exposures[id] = {
      rSquared: maybeNum(e.r_squared, `exposures.${id}.r_squared`),
      regressionDays: Number(e.regression_days),
      droppedCollinear: typeof e.dropped_collinear === "string" ? e.dropped_collinear : "",
      factors,
    };
  }
  const correlations: Record<string, Record<string, number | null>> = {};
  for (const [w, rawW] of Object.entries(obj(f.correlations, "correlations"))) {
    correlations[w] = {};
    for (const [pair, v] of Object.entries(obj(rawW, `correlations.${w}`))) {
      correlations[w][pair] = maybeNum(v, `correlations.${w}.${pair}`);
    }
  }
  const leaders: Record<string, Record<string, Leader[]>> = {};
  for (const [id, rawL] of Object.entries(obj(f.stock_leaders, "stock_leaders"))) {
    leaders[id] = {};
    for (const [factor, list] of Object.entries(obj(rawL, `stock_leaders.${id}`))) {
      if (!Array.isArray(list)) throw new Error(`stock_leaders.${id}.${factor} is malformed`);
      leaders[id][factor] = list.map((x, i) => {
        const r = obj(x, `stock_leaders.${id}.${factor}[${i}]`);
        return {
          ticker: String(r.ticker),
          beta63d: maybeNum(r.beta_63d, "beta_63d"),
          corr63d: maybeNum(r.corr_63d, "corr_63d"),
        };
      });
    }
  }
  return {
    methodologyVersion: f.methodology_version,
    source: f.source,
    indexIds: strArray(f.index_ids, "index_ids"),
    series: strArray(f.series, "series"),
    catalog,
    exposures,
    correlations,
    leaders,
  };
}

export async function getFactors(): Promise<FactorsPageData> {
  if (isFixtureMode()) return parseFactorsFixture(factorsFixture);
  throw new ConfigError(
    "The /factors library reads the correlation model's exported fixture; it is not stored in " +
      "Supabase. Run with ALPHASTOCK_DATA_SOURCE=fixture.",
  );
}

/** Symmetric lookup: the matrix stores each pair once. Diagonal is exactly 1. */
export function correlationOf(
  matrix: Record<string, number | null>,
  a: string,
  b: string,
): number | null {
  if (a === b) return 1;
  const v = matrix[`${a}|${b}`] ?? matrix[`${b}|${a}`];
  return v === undefined ? null : v;
}

/** Signed fixed-decimals; empty (not zero) when the statistic is not estimable. */
export function fmt(value: number | null, digits = 2): string {
  if (value === null || !Number.isFinite(value)) return "";
  const s = Math.abs(value).toFixed(digits);
  return value < 0 ? `−${s}` : s;
}

/**
 * Diverging cell fill: blue for positive, orange for negative, near-neutral
 * grey at zero. Tints stay light enough that dark text keeps contrast.
 */
export function heatColor(value: number | null): string {
  if (value === null) return "transparent";
  const v = Math.max(-1, Math.min(1, value));
  const [r, g, b] = v >= 0 ? [42, 120, 214] : [235, 104, 52];
  const alpha = (0.08 + 0.55 * Math.abs(v)).toFixed(3);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}
