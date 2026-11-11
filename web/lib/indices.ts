/**
 * Index comparison data for the /indices view.
 *
 * The AS-100 / AS-MC100 series come from the pipeline's exported artifacts
 * (pipelines/pipelines/indices → comparison.json + index_metadata.json,
 * merged into fixtures/index.json by pipelines.indices.web_fixture). In
 * fixture mode the committed export is served; there is no Supabase path
 * yet — benchmark proxy series are pipeline exports, not stored tables —
 * so supabase mode fails loudly rather than rendering a partial view.
 */
import indexFixture from "@/fixtures/index.json";
import { ConfigError, isFixtureMode } from "./data";

export interface BenchmarkView {
  /** Tiingo symbol standing in for the licensed benchmark. */
  proxy: string;
  /** Human name, states the proxy relationship explicitly. */
  name: string;
  /** ISO date → level, rebased to 1000 at the index's base date. */
  levels: Record<string, number>;
}

export interface IndexView {
  indexId: string;
  baseDate: string;
  constituentCount: number;
  /** ISO date → level; sorted keys are the trading-day series. */
  levels: Record<string, number>;
  benchmarks: BenchmarkView[];
  /** Documented exclusions (e.g. PSTG's Tiingo EOD gap) — footnoted, not hidden. */
  excluded: { ticker: string; reason: string }[];
}

export interface IndicesPageData {
  methodologyVersion: string;
  source: string;
  proxyMapping: Record<string, string>;
  indices: IndexView[];
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** Parse the pipeline export's string levels into numbers; bad rows fail loudly. */
function parseLevels(raw: unknown, label: string): Record<string, number> {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    throw new Error(`${label} is malformed: expected an object of date → level`);
  }
  const out: Record<string, number> = {};
  for (const [date, value] of Object.entries(raw)) {
    if (!ISO_DATE.test(date) || typeof value !== "string" || !Number.isFinite(Number(value))) {
      throw new Error(`${label}[${date}] is malformed`);
    }
    out[date] = Number(value);
  }
  return out;
}

function parseBenchmark(raw: unknown, label: string): BenchmarkView {
  if (typeof raw !== "object" || raw === null) {
    throw new Error(`${label} is malformed`);
  }
  const record = raw as { proxy?: unknown; name?: unknown; levels?: unknown };
  if (typeof record.proxy !== "string" || typeof record.name !== "string") {
    throw new Error(`${label} is malformed: missing proxy or name`);
  }
  return {
    proxy: record.proxy,
    name: record.name,
    levels: parseLevels(record.levels, `${label}.levels`),
  };
}

/** Map the committed fixture JSON into the typed view; malformed rows fail the page. */
export function parseIndexFixture(raw: unknown): IndicesPageData {
  if (typeof raw !== "object" || raw === null) {
    throw new Error("index fixture is malformed");
  }
  const fixture = raw as {
    methodology_version?: unknown;
    source?: unknown;
    benchmark_proxy_mapping?: unknown;
    indices?: unknown;
  };
  if (
    typeof fixture.methodology_version !== "string" ||
    typeof fixture.source !== "string" ||
    typeof fixture.benchmark_proxy_mapping !== "object" ||
    fixture.benchmark_proxy_mapping === null ||
    typeof fixture.indices !== "object" ||
    fixture.indices === null ||
    Array.isArray(fixture.indices)
  ) {
    throw new Error("index fixture is malformed: missing required fields");
  }

  const indices: IndexView[] = Object.entries(fixture.indices).map(([indexId, rawIndex]) => {
    if (typeof rawIndex !== "object" || rawIndex === null) {
      throw new Error(`index fixture ${indexId} is malformed`);
    }
    const index = rawIndex as {
      base_date?: unknown;
      constituent_count?: unknown;
      levels?: unknown;
      benchmarks?: unknown;
      excluded?: unknown;
    };
    if (typeof index.base_date !== "string" || typeof index.constituent_count !== "number") {
      throw new Error(`index fixture ${indexId} is malformed: missing base fields`);
    }
    if (typeof index.benchmarks !== "object" || index.benchmarks === null) {
      throw new Error(`index fixture ${indexId} is malformed: missing benchmarks`);
    }
    const excluded = (Array.isArray(index.excluded) ? index.excluded : []).map(
      (row: unknown) => {
        if (typeof row !== "object" || row === null) {
          throw new Error(`index fixture ${indexId} has a malformed exclusion`);
        }
        const entry = row as { ticker?: unknown; reason?: unknown };
        if (typeof entry.ticker !== "string" || typeof entry.reason !== "string") {
          throw new Error(`index fixture ${indexId} has a malformed exclusion`);
        }
        return { ticker: entry.ticker, reason: entry.reason };
      },
    );
    return {
      indexId,
      baseDate: index.base_date,
      constituentCount: index.constituent_count,
      levels: parseLevels(index.levels, `index fixture ${indexId}.levels`),
      benchmarks: Object.entries(index.benchmarks).map(([proxy, rawBenchmark]) =>
        parseBenchmark(rawBenchmark, `index fixture ${indexId}.benchmarks.${proxy}`),
      ),
      excluded,
    };
  });

  return {
    methodologyVersion: fixture.methodology_version,
    source: fixture.source,
    proxyMapping: fixture.benchmark_proxy_mapping as Record<string, string>,
    indices,
  };
}

/** Load the /indices view. Supabase mode has no benchmark-proxy store yet — fail loudly. */
export async function getIndices(): Promise<IndicesPageData> {
  if (isFixtureMode()) {
    return parseIndexFixture(indexFixture);
  }
  throw new ConfigError(
    "The /indices comparison view reads pipeline-exported series; benchmark proxies are " +
      "not stored in Supabase yet. Run with ALPHASTOCK_DATA_SOURCE=fixture for the demo.",
  );
}

// -- pure display helpers (vitest-covered) -----------------------------------

/** Trading dates of a levels record, sorted ascending. */
export function levelDates(levels: Record<string, number>): string[] {
  return Object.keys(levels).sort();
}

/**
 * Percent return from the base level to the last level; null when not
 * computable. With a baseDate, the anchor is that date's level (the
 * methodology's base-1000 anchor); without one, the first trading date.
 */
export function sinceBaseReturn(
  levels: Record<string, number>,
  baseDate?: string,
): number | null {
  const dates = levelDates(levels);
  if (dates.length === 0) return null;
  const base =
    baseDate !== undefined && levels[baseDate] !== undefined
      ? levels[baseDate]
      : levels[dates[0]];
  if (!Number.isFinite(base) || base === 0) return null;
  return (levels[dates[dates.length - 1]] / base - 1) * 100;
}

/** "1,055.5" — one decimal keeps the free-tier display rule (no modeling precision). */
export function formatLevel(level: number): string {
  if (!Number.isFinite(level)) return "—";
  return level.toLocaleString("en-US", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  });
}

/** Signed one-decimal percent, "+5.6%" / "−0.8%"; matches formatDelta's minus sign. */
export function formatReturnPct(percent: number | null): string {
  if (percent === null || !Number.isFinite(percent)) return "—";
  if (percent > 0) return `+${percent.toFixed(1)}%`;
  if (percent < 0) return `−${Math.abs(percent).toFixed(1)}%`;
  return "0.0%";
}

/**
 * SVG polyline points for a levels series, x left→right by date, y inverted
 * (higher level = higher on screen). Single-point series get a centered dot
 * position so the chart never produces NaN.
 */
export function sparklinePoints(
  levels: Record<string, number>,
  width: number,
  height: number,
  pad: number,
): string {
  const dates = levelDates(levels);
  if (dates.length === 0) return "";
  const values = dates.map((d) => levels[d]);
  const innerW = width - 2 * pad;
  const innerH = height - 2 * pad;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min;
  return dates
    .map((date, i) => {
      const x = dates.length === 1 ? width / 2 : pad + (i * innerW) / (dates.length - 1);
      const y =
        span === 0
          ? height / 2
          : pad + innerH - ((values[i] - min) / span) * innerH;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}
