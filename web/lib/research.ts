/**
 * Research-frame backfill data for the /research view.
 *
 * Served from fixtures/research.json, written by
 * `python -m pipelines.indices.research --web-out ../web/fixtures/research.json`
 * (pipelines/pipelines/indices/research.py). It is an internal research view —
 * a current-constituent backtest from 2020-01-02 — never a published index
 * value, and the page says so before any number.
 *
 * Every index also carries precomputed periods (since-2020, 5Y/3Y/1Y, YTD and
 * each complete calendar year). Each is measured from the close before it
 * starts, so the page can show any period's chart and tables without
 * recomputing the index in the browser.
 */
import researchFixture from "@/fixtures/research.json";
import { ConfigError, isFixtureMode } from "./data";

export interface SeriesStats {
  start: string;
  end: string;
  totalReturn: number;
  cagr: number;
  annVol: number;
  returnToVol: number;
  maxDrawdown: number;
  maxDrawdownPeak: string;
  maxDrawdownTrough: string;
  calendarReturns: Record<string, number>;
}

export interface ResearchBenchmark {
  proxy: string;
  name: string;
  stats: SeriesStats;
  levels: Record<string, number>;
}

export interface CategoryRow {
  category: string;
  constituents: number;
  totalReturn: number;
  cagr: number;
  annVol: number;
  maxDrawdown: number;
}

export interface CorporateEvent {
  ticker: string;
  date: string;
  event: string;
  detail: string;
  indexTreatment: string;
  status: string;
  source: string;
  /** Close move on the effective date when the adjustment was verified in the frame. */
  frameMove: number | null;
}

export type PeriodKind = "max" | "trailing" | "ytd" | "year";

export interface ResearchPeriod {
  /** "max", "5y", "3y", "1y", "ytd" or a calendar year ("2023"). */
  id: string;
  kind: PeriodKind;
  /** Close the period is measured from (prior close; the frame base for "max"/first year). */
  base: string;
  end: string;
  stats: SeriesStats;
  benchmarks: Record<string, SeriesStats>;
  categories: CategoryRow[];
}

export interface ResearchIndexView {
  indexId: string;
  baseDate: string;
  constituentsAtBase: number;
  constituentsAtEnd: number;
  entries: { date: string; ticker: string }[];
  excluded: { ticker: string; reason: string }[];
  eventCounts: Record<string, number>;
  stats: SeriesStats;
  categories: CategoryRow[];
  corporateEvents: CorporateEvent[];
  broadBenchmarks: string[];
  thematicBenchmarks: string[];
  levels: Record<string, number>;
  benchmarks: Record<string, ResearchBenchmark>;
  periods: ResearchPeriod[];
}

export interface ResearchPageData {
  frame: { start: string; end: string };
  source: string;
  methodologyVersion: string;
  caveats: string[];
  caveatsZh: string[];
  universeOverlap: string[];
  /** Names AS-MCG100 and AS-MCB100 share by design (same bands, different weights). */
  mixedCapShared: number;
  listingDates: Record<string, string>;
  indices: ResearchIndexView[];
}

type Raw = Record<string, unknown>;

function obj(raw: unknown, label: string): Raw {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    throw new Error(`${label} is malformed: expected an object`);
  }
  return raw as Raw;
}

function num(raw: unknown, label: string): number {
  if (typeof raw !== "number" || !Number.isFinite(raw)) throw new Error(`${label} is not a number`);
  return raw;
}

function str(raw: unknown, label: string): string {
  if (typeof raw !== "string") throw new Error(`${label} is not a string`);
  return raw;
}

function numRecord(raw: unknown, label: string): Record<string, number> {
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(obj(raw, label))) out[k] = num(v, `${label}[${k}]`);
  return out;
}

function parseStats(raw: unknown, label: string): SeriesStats {
  const s = obj(raw, label);
  return {
    start: str(s.start, `${label}.start`),
    end: str(s.end, `${label}.end`),
    totalReturn: num(s.total_return, `${label}.total_return`),
    cagr: num(s.cagr, `${label}.cagr`),
    annVol: num(s.ann_vol, `${label}.ann_vol`),
    returnToVol: num(s.return_to_vol, `${label}.return_to_vol`),
    maxDrawdown: num(s.max_drawdown, `${label}.max_drawdown`),
    maxDrawdownPeak: str(s.max_drawdown_peak, `${label}.max_drawdown_peak`),
    maxDrawdownTrough: str(s.max_drawdown_trough, `${label}.max_drawdown_trough`),
    calendarReturns: numRecord(s.calendar_returns, `${label}.calendar_returns`),
  };
}

const PERIOD_KINDS: readonly PeriodKind[] = ["max", "trailing", "ytd", "year"];

function parseCategory(raw: unknown, label: string): CategoryRow {
  const r = obj(raw, label);
  return {
    category: str(r.category, `${label}.category`),
    constituents: num(r.constituents, `${label}.constituents`),
    totalReturn: num(r.total_return, `${label}.total_return`),
    cagr: num(r.cagr, `${label}.cagr`),
    annVol: num(r.ann_vol, `${label}.ann_vol`),
    maxDrawdown: num(r.max_drawdown, `${label}.max_drawdown`),
  };
}

function parsePeriod(raw: unknown, label: string): ResearchPeriod {
  const p = obj(raw, label);
  const kind = str(p.kind, `${label}.kind`) as PeriodKind;
  if (!PERIOD_KINDS.includes(kind)) throw new Error(`${label}.kind is unknown: ${kind}`);
  if (!Array.isArray(p.categories)) throw new Error(`${label}.categories is malformed`);
  const benchmarks: Record<string, SeriesStats> = {};
  for (const [proxy, s] of Object.entries(obj(p.benchmarks, `${label}.benchmarks`))) {
    benchmarks[proxy] = parseStats(s, `${label}.benchmarks.${proxy}`);
  }
  return {
    id: str(p.id, `${label}.id`),
    kind,
    base: str(p.base, `${label}.base`),
    end: str(p.end, `${label}.end`),
    stats: parseStats(p.stats, `${label}.stats`),
    benchmarks,
    categories: p.categories.map((c, n) => parseCategory(c, `${label}.categories[${n}]`)),
  };
}

/** Map the committed fixture into typed views; malformed data fails the page loudly. */
export function parseResearchFixture(raw: unknown): ResearchPageData {
  const f = obj(raw, "research fixture");
  const frame = obj(f.frame, "research fixture.frame");
  const indices = Object.entries(obj(f.indices, "research fixture.indices")).map(
    ([indexId, rawIndex]): ResearchIndexView => {
      const label = `research fixture ${indexId}`;
      const i = obj(rawIndex, label);
      const benchmarks: Record<string, ResearchBenchmark> = {};
      for (const [proxy, rawB] of Object.entries(obj(i.benchmarks, `${label}.benchmarks`))) {
        const b = obj(rawB, `${label}.benchmarks.${proxy}`);
        benchmarks[proxy] = {
          proxy,
          name: str(b.name, `${label}.benchmarks.${proxy}.name`),
          stats: parseStats(b.stats, `${label}.benchmarks.${proxy}.stats`),
          levels: numRecord(b.levels, `${label}.benchmarks.${proxy}.levels`),
        };
      }
      const list = (key: string) => {
        const v = i[key];
        if (!Array.isArray(v)) throw new Error(`${label}.${key} is malformed`);
        return v as unknown[];
      };
      return {
        indexId,
        baseDate: str(i.base_date, `${label}.base_date`),
        constituentsAtBase: num(i.constituents_at_base, `${label}.constituents_at_base`),
        constituentsAtEnd: num(i.constituents_at_end, `${label}.constituents_at_end`),
        entries: list("entries").map((e, n) => {
          const r = obj(e, `${label}.entries[${n}]`);
          return { date: str(r.date, "entry.date"), ticker: str(r.ticker, "entry.ticker") };
        }),
        excluded: list("excluded").map((e, n) => {
          const r = obj(e, `${label}.excluded[${n}]`);
          return { ticker: str(r.ticker, "excluded.ticker"), reason: str(r.reason, "excluded.reason") };
        }),
        eventCounts: numRecord(i.event_counts, `${label}.event_counts`),
        stats: parseStats(i.stats, `${label}.stats`),
        categories: list("categories").map((c, n) => parseCategory(c, `${label}.categories[${n}]`)),
        corporateEvents: (Array.isArray(i.corporate_events) ? i.corporate_events : []).map(
          (e, n) => {
            const r = obj(e, `${label}.corporate_events[${n}]`);
            const l = `${label}.corporate_events[${n}]`;
            return {
              ticker: str(r.ticker, `${l}.ticker`),
              date: str(r.date, `${l}.date`),
              event: str(r.event, `${l}.event`),
              detail: str(r.detail, `${l}.detail`),
              indexTreatment: str(r.index_treatment, `${l}.index_treatment`),
              status: str(r.status, `${l}.status`),
              source: str(r.source, `${l}.source`),
              frameMove: r.frame_move === undefined ? null : num(r.frame_move, `${l}.frame_move`),
            };
          },
        ),
        broadBenchmarks: list("broad_benchmarks").map((p) => str(p, "broad_benchmarks[]")),
        thematicBenchmarks: list("thematic_benchmarks").map((p) => str(p, "thematic_benchmarks[]")),
        levels: numRecord(i.levels, `${label}.levels`),
        benchmarks,
        periods: list("periods").map((p, n) => parsePeriod(p, `${label}.periods[${n}]`)),
      };
    },
  );
  const caveats = f.caveats;
  const caveatsZh = f.caveats_zh;
  const overlap = f.universe_overlap;
  if (!Array.isArray(caveats) || !Array.isArray(caveatsZh) || !Array.isArray(overlap)) {
    throw new Error("research fixture is malformed: caveats/caveats_zh/universe_overlap");
  }
  if (caveats.length !== caveatsZh.length) {
    throw new Error("research fixture is malformed: EN and 中文 caveats differ in length");
  }
  return {
    frame: { start: str(frame.start, "frame.start"), end: str(frame.end, "frame.end") },
    source: str(f.source, "source"),
    methodologyVersion: str(f.methodology_version, "methodology_version"),
    caveats: caveats.map((c) => str(c, "caveat")),
    caveatsZh: caveatsZh.map((c) => str(c, "caveat_zh")),
    universeOverlap: overlap.map((t) => str(t, "universe_overlap[]")),
    mixedCapShared: num(f.mixed_cap_shared, "mixed_cap_shared"),
    listingDates: Object.fromEntries(
      Object.entries(obj(f.listing_dates, "listing_dates")).map(([k, v]) => [k, str(v, k)]),
    ),
    indices,
  };
}

export async function getResearch(): Promise<ResearchPageData> {
  if (isFixtureMode()) return parseResearchFixture(researchFixture);
  throw new ConfigError(
    "The /research backfill reads the pipeline-exported research fixture; it is not stored " +
      "in Supabase. Run with ALPHASTOCK_DATA_SOURCE=fixture.",
  );
}

// -- pure display helpers (vitest-covered) -----------------------------------

/** Signed one-decimal percent from a fraction: 0.1234 → "+12.3%". */
export function pct(fraction: number): string {
  if (!Number.isFinite(fraction)) return "—";
  const p = fraction * 100;
  if (Math.abs(p) < 0.05) return "0.0%";
  return p > 0 ? `+${p.toFixed(1)}%` : `−${Math.abs(p).toFixed(1)}%`;
}

/** Unsigned one-decimal percent for magnitudes (volatility). */
export function pctAbs(fraction: number): string {
  return Number.isFinite(fraction) ? `${(fraction * 100).toFixed(1)}%` : "—";
}

export interface ChartSeries {
  key: string;
  label: string;
  /** Aligned to the chart's date axis; null where the series has no close. */
  values: (number | null)[];
}

/** The selected period, falling back to the full frame for an unknown or missing id. */
export function resolvePeriod(index: ResearchIndexView, id: string | undefined): ResearchPeriod {
  const max = index.periods.find((p) => p.id === "max");
  if (!max) throw new Error(`research fixture ${index.indexId} has no "max" period`);
  return index.periods.find((p) => p.id === id) ?? max;
}

/** Rebase a series so its first observed value in the window is 1000. */
function rebase(values: (number | null)[]): (number | null)[] {
  const first = values.find((v): v is number => v !== null && v > 0);
  if (first === undefined) return values.map(() => null);
  return values.map((v) => (v === null ? null : Math.round((v / first) * 1000 * 100) / 100));
}

/**
 * Align the index and its broad benchmarks onto the index's trading dates.
 * With a period, the axis is cut to [base, end] and every series is rebased
 * to 1000 on the period's base close, so the chart starts level for all lines.
 */
export function chartSeries(
  index: ResearchIndexView,
  period?: Pick<ResearchPeriod, "id" | "base" | "end">,
): { dates: string[]; series: ChartSeries[] } {
  const all = Object.keys(index.levels).sort();
  const sliced = period && period.id !== "max";
  const dates = sliced ? all.filter((d) => d >= period.base && d <= period.end) : all;
  const raw: ChartSeries[] = [
    { key: index.indexId, label: index.indexId, values: dates.map((d) => index.levels[d]) },
    ...index.broadBenchmarks.map((proxy) => {
      const b = index.benchmarks[proxy];
      return {
        key: proxy,
        label: proxy,
        values: dates.map((d) => (b && b.levels[d] !== undefined ? b.levels[d] : null)),
      };
    }),
  ];
  const series = sliced ? raw.map((s) => ({ ...s, values: rebase(s.values) })) : raw;
  return { dates, series };
}

/** "Nice" axis ticks covering [min, max]. */
export function niceTicks(min: number, max: number, count = 5): number[] {
  if (!(max > min)) return [min];
  const raw = (max - min) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  const top = Math.ceil(max / step - 1e-9) * step;
  for (let v = Math.floor(min / step) * step; v <= top + 1e-9; v += step) out.push(v);
  return out;
}
