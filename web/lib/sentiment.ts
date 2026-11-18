/**
 * Parser and derivations for Market Sentiment scan output — the
 * `sentiment_runs` / `sentiment_posts` tables staged by
 * `pipelines/pipelines/sentiment/run_scan.py` (or their fixture twins in
 * `web/fixtures/sentiment.json`).
 *
 * Rows come from PostgREST or fixture JSON — never trusted. Every field is
 * validated eagerly and a malformed row throws SentimentParseError, so a bad
 * payload fails the page instead of rendering a silent half-truth (same
 * loud-failure convention as `lib/findings.ts` and `lib/view.ts`).
 */

export const SENTIMENT_PLATFORMS = ["reddit", "x-indexed", "linkedin-indexed"] as const;
export type SentimentPlatform = (typeof SENTIMENT_PLATFORMS)[number];

export interface SentimentRunRow {
  runId: string;
  asOf: string;
  topicId: string;
  labelEn: string;
  labelZh: string;
  postCount: number;
  /** Mean raw score of the run's posts, in [-1, +1]. */
  topicScore: number;
  /** 0..1 — item count and source spread, computed by the pipeline. */
  confidence: number;
  platformCounts: Record<SentimentPlatform, number>;
}

export interface SentimentPostRow {
  runId: string;
  topicId: string;
  platform: SentimentPlatform;
  author: string;
  url: string;
  postDate: string | null;
  snippet: string;
  rawScore: number;
}

export interface SentimentData {
  /** Latest scan timestamp across all run rows (rows are append-only). */
  latestAsOf: string;
  /** Run rows of the latest scan, in staged order. */
  runs: SentimentRunRow[];
  /** Scored posts of the latest scan. */
  posts: SentimentPostRow[];
  /** post_count per topic in the run before the latest; absent = first window. */
  previousCounts: Record<string, number>;
}

export class SentimentParseError extends Error {
  constructor(message: string) {
    super(`sentiment payload is malformed: ${message}`);
    this.name = "SentimentParseError";
  }
}

function asRecord(row: unknown, where: string): Record<string, unknown> {
  if (typeof row !== "object" || row === null || Array.isArray(row)) {
    throw new SentimentParseError(`${where} must be an object`);
  }
  return row as Record<string, unknown>;
}

function requireString(record: Record<string, unknown>, key: string, where: string): string {
  const value = record[key];
  if (typeof value !== "string" || value.length === 0) {
    throw new SentimentParseError(`${where}.${key} must be a non-empty string`);
  }
  return value;
}

function requireInt(record: Record<string, unknown>, key: string, where: string, min: number, max: number): number {
  const value = record[key];
  if (typeof value !== "number" || !Number.isInteger(value) || value < min || value > max) {
    throw new SentimentParseError(`${where}.${key} must be an integer in [${min}, ${max}]`);
  }
  return value;
}

function requireScore(record: Record<string, unknown>, key: string, where: string): number {
  const value = record[key];
  if (typeof value !== "number" || !Number.isFinite(value) || value < -1 || value > 1) {
    throw new SentimentParseError(`${where}.${key} must be a finite number in [-1, 1]`);
  }
  return value;
}

function parsePlatform(value: unknown, where: string): SentimentPlatform {
  if (typeof value !== "string" || !SENTIMENT_PLATFORMS.includes(value as SentimentPlatform)) {
    throw new SentimentParseError(`${where}.platform must be one of ${SENTIMENT_PLATFORMS.join(", ")}`);
  }
  return value as SentimentPlatform;
}

const ISO_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

function parseRun(raw: unknown, index: number): SentimentRunRow {
  const where = `runs[${index}]`;
  const record = asRecord(raw, where);
  const asOf = requireString(record, "as_of", where);
  if (!ISO_TIMESTAMP.test(asOf)) {
    throw new SentimentParseError(`${where}.as_of must be an ISO-8601 UTC timestamp (…Z)`);
  }
  const platformCountsRaw = record.platform_counts;
  if (typeof platformCountsRaw !== "object" || platformCountsRaw === null || Array.isArray(platformCountsRaw)) {
    throw new SentimentParseError(`${where}.platform_counts must be an object`);
  }
  const countsRecord = platformCountsRaw as Record<string, unknown>;
  const platformCounts = Object.fromEntries(
    SENTIMENT_PLATFORMS.map((platform) => [
      platform,
      requireInt(countsRecord, platform, `${where}.platform_counts`, 0, 1_000_000),
    ]),
  ) as Record<SentimentPlatform, number>;
  return {
    runId: requireString(record, "run_id", where),
    asOf,
    topicId: requireString(record, "topic_id", where),
    labelEn: requireString(record, "label_en", where),
    labelZh: requireString(record, "label_zh", where),
    postCount: requireInt(record, "post_count", where, 0, 1_000_000),
    topicScore: requireScore(record, "topic_score", where),
    confidence: requireScore(record, "confidence", where),
    platformCounts,
  };
}

function parsePost(raw: unknown, index: number): SentimentPostRow {
  const where = `posts[${index}]`;
  const record = asRecord(raw, where);
  const url = requireString(record, "url", where);
  if (!url.startsWith("https://")) {
    throw new SentimentParseError(`${where}.url must be an absolute https URL`);
  }
  const postDateRaw = record.post_date;
  if (postDateRaw !== null && postDateRaw !== undefined) {
    if (typeof postDateRaw !== "string" || !ISO_DATE.test(postDateRaw)) {
      throw new SentimentParseError(`${where}.post_date must be YYYY-MM-DD or null`);
    }
  }
  return {
    runId: requireString(record, "run_id", where),
    topicId: requireString(record, "topic_id", where),
    platform: parsePlatform(record.platform, where),
    author: requireString(record, "author", where),
    url,
    postDate: (postDateRaw as string | null) ?? null,
    snippet: requireString(record, "snippet", where),
    rawScore: requireScore(record, "raw_score", where),
  };
}

/** Validate a raw {runs, posts} payload; collapses to the latest scan window. */
export function parseSentimentPayload(raw: unknown): SentimentData {
  const record = asRecord(raw, "payload");
  if (!Array.isArray(record.runs) || !Array.isArray(record.posts)) {
    throw new SentimentParseError("payload.runs and payload.posts must be arrays");
  }
  const runs = record.runs.map(parseRun);
  const posts = record.posts.map(parsePost);
  if (runs.length === 0) {
    throw new SentimentParseError("payload.runs must not be empty");
  }
  const latestAsOf = runs.reduce((max, run) => (run.asOf > max ? run.asOf : max), runs[0].asOf);
  const latest = runs.filter((run) => run.asOf === latestAsOf);
  const latestRunId = latest[0].runId;
  const previous = runs.filter((run) => run.asOf < latestAsOf);
  const previousAsOf = previous.reduce((max, run) => (run.asOf > max ? run.asOf : max), "");
  const previousCounts = Object.fromEntries(
    previous.filter((run) => run.asOf === previousAsOf).map((run) => [run.topicId, run.postCount]),
  );
  return {
    latestAsOf,
    runs: latest,
    posts: posts.filter((post) => post.runId === latestRunId),
    previousCounts,
  };
}

export type ConfidenceBand = "high" | "medium" | "low";

/** Confidence bands mirror the pipeline's count/spread model (0..1). */
export function confidenceBand(confidence: number): ConfidenceBand {
  if (confidence >= 0.66) return "high";
  if (confidence >= 0.4) return "medium";
  return "low";
}

export type SentimentBand = "strong-positive" | "positive" | "neutral" | "negative" | "strong-negative";

/**
 * Qualitative reading of a -1..+1 score. Free-tier display rules (design doc
 * §6) bar two-decimal modeling numbers on public pages, so scores surface as
 * a band — the numeric value stays in the gauge's proportional bar only.
 */
export function sentimentBand(score: number): SentimentBand {
  if (score >= 0.5) return "strong-positive";
  if (score >= 0.15) return "positive";
  if (score > -0.15) return "neutral";
  if (score > -0.5) return "negative";
  return "strong-negative";
}

/** Latest post_count minus the previous window's; null on a first scan. */
export function volumeDelta(row: SentimentRunRow, previousCounts: Record<string, number>): number | null {
  const previous = previousCounts[row.topicId];
  return previous === undefined ? null : row.postCount - previous;
}
