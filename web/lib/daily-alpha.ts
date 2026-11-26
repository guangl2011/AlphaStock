/**
 * Daily Alpha digest (§7.2): the bilingual brief the agent pipeline stages
 * (pipelines/agents/daily_alpha.py → brief.json, one GitHub issue per day).
 * The web renders the staged artifact; it never builds briefs itself.
 *
 * There is no Supabase store for briefs yet — the page runs on the fixture
 * slice in fixture mode and fails explicitly in supabase mode, like /factors
 * and /indices before their stores existed.
 */
import dailyAlphaFixture from "@/fixtures/daily-alpha.json";
import { ConfigError, isFixtureMode } from "./data";
import type { PublicSignal } from "./types";
import { toPublicSignal } from "./view";

export interface DailyAlphaScoreDelta {
  ticker: string;
  score: number;
  previous: number;
  delta: number;
}

export interface DailyAlphaBrief {
  asOf: string;
  titleEn: string;
  titleZh: string;
  takeawayEn: string;
  takeawayZh: string;
  /** Canonical signal rows, parsed through the same mapper as /feed. */
  items: PublicSignal[];
  themeHeat: Array<{ theme: string; count: number }>;
  macroEn: string;
  macroZh: string;
  scores: Record<string, DailyAlphaScoreDelta>;
}

/** The fixture payload: `{"briefs": [...]}` — a list of staged brief.json rows. */
export function parseBriefs(payload: unknown): DailyAlphaBrief[] {
  const briefs = (payload as { briefs: unknown }).briefs;
  if (!Array.isArray(briefs)) {
    throw new Error("daily-alpha fixture: expected a {briefs: [...]} payload");
  }
  return briefs.map(parseBrief);
}

function parseBrief(row: unknown): DailyAlphaBrief {
  const r = row as Record<string, unknown>;
  const required = ["as_of", "title_en", "title_zh", "takeaway_en", "takeaway_zh", "macro_en", "macro_zh"];
  for (const key of required) {
    if (typeof r[key] !== "string" || !(r[key] as string).trim()) {
      throw new Error(`daily-alpha brief: missing or empty "${key}"`);
    }
  }
  if (!Array.isArray(r.items) || r.items.length === 0) {
    throw new Error(`daily-alpha brief ${r.title_en as string}: no items`);
  }
  const themeHeat = (r.theme_heat as Array<[string, number]>).map(([theme, count]) => ({
    theme,
    count,
  }));
  const scores: Record<string, DailyAlphaScoreDelta> = {};
  for (const [ticker, delta] of Object.entries(r.scores as Record<string, DailyAlphaScoreDelta>)) {
    scores[ticker] = delta;
  }
  return {
    asOf: r.as_of as string,
    titleEn: r.title_en as string,
    titleZh: r.title_zh as string,
    takeawayEn: r.takeaway_en as string,
    takeawayZh: r.takeaway_zh as string,
    items: (r.items as unknown[]).map(toPublicSignal),
    themeHeat,
    macroEn: r.macro_en as string,
    macroZh: r.macro_zh as string,
    scores,
  };
}

/** The most recent staged brief by as_of — the page's only candidate. */
export function latestBrief(briefs: DailyAlphaBrief[]): DailyAlphaBrief | null {
  return briefs.reduce<DailyAlphaBrief | null>(
    (latest, brief) => (latest === null || brief.asOf > latest.asOf ? brief : latest),
    null,
  );
}

/** UTC-date equality: a brief is "today's" only on the day it was staged. */
export function isFromToday(asOf: string, now: Date): boolean {
  return asOf.slice(0, 10) === now.toISOString().slice(0, 10);
}

/** Free-tier display rule: Alpha Score moves read as a qualitative tilt. */
export type ScoreTilt = "improving" | "steady" | "softening";

export function scoreTilt(delta: number): ScoreTilt {
  if (delta > 0.5) return "improving";
  if (delta < -0.5) return "softening";
  return "steady";
}

/** Bar width as a share of the hottest theme — heat is comparative. */
export function heatPercent(count: number, max: number): number {
  if (max <= 0) return 0;
  return Math.round((count / max) * 100);
}

/**
 * Fixture-mode data source. In supabase mode, fail honestly: no brief store
 * exists yet, and silently rendering yesterday's data would be dishonest.
 */
export function getDailyAlpha(): { brief: DailyAlphaBrief | null; fixture: boolean } {
  if (!isFixtureMode()) {
    throw new ConfigError(
      "Daily Alpha briefs have no Supabase store yet — run with ALPHASTOCK_DATA_SOURCE=fixture.",
    );
  }
  const briefs = parseBriefs(dailyAlphaFixture);
  return { brief: latestBrief(briefs), fixture: true };
}
