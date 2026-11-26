/**
 * Watchlist (V2.1 §7.2) — pure logic: tier limits, ticker validation, and
 * row building. No I/O here; the store boundary lives in watchlist-store.ts.
 *
 * The row shape intentionally matches the Screener's `ScreenerRow` (same
 * field names for the latest signal, Alpha Score, and delta) so the two
 * tables can share markup conventions without cross-imports.
 */
import type { Messages } from "./messages";
import type { PublicAlert, PublicCompany, PublicSignal, Sensitivity } from "./types";

/** Subscription tiers from the V2.1 spec (§ Watchlist limits). */
export type Tier = "explorer" | "pro" | "pro_plus" | "institutional";

const TIERS: Tier[] = ["explorer", "pro", "pro_plus", "institutional"];

/**
 * Placeholder tier until billing exists: every signed-in user is treated as
 * Pro (V2.1 blueprint acceptance). ALPHASTOCK_USER_TIER overrides it — that is
 * how the at-limit state is demonstrated without a paid plan.
 */
export function currentTier(): Tier {
  const raw = process.env.ALPHASTOCK_USER_TIER;
  return TIERS.includes(raw as Tier) ? (raw as Tier) : "pro";
}

/** Max tickers per tier; null = unlimited (Institutional). */
export function watchlistLimit(tier: Tier): number | null {
  switch (tier) {
    case "explorer":
      return 5;
    case "pro":
      return 30;
    case "pro_plus":
      return 100;
    case "institutional":
      return null;
  }
}

/** Alert channels the per-ticker setting can target (delivery is out of V2.1 scope). */
export type AlertChannel = "in_app" | "email";

export const ALERT_CHANNELS: AlertChannel[] = ["in_app", "email"];

export function parseAlertChannel(raw: string | undefined | null): AlertChannel | null {
  return ALERT_CHANNELS.includes(raw as AlertChannel) ? (raw as AlertChannel) : null;
}

/** Tickers are 1–6 uppercase letters; input is normalized before validation. */
export function normalizeTicker(raw: string | undefined | null): string {
  return (raw ?? "").trim().toUpperCase();
}

export function isValidTicker(ticker: string): boolean {
  return /^[A-Z]{1,6}$/.test(ticker);
}

export type AddTickerOutcome =
  | { ok: true; tickers: string[] }
  | { ok: false; reason: "invalid" | "duplicate" | "limit"; limit: number | null };

/** One per-ticker alert preference — mirrors the 0004 user_alert_settings row. */
export interface AlertSetting {
  ticker: string;
  channel: AlertChannel;
  enabled: boolean;
}

/** The whole add decision in one pure function — the action only persists. */
export function applyAdd(current: string[], raw: string, tier: Tier): AddTickerOutcome {
  const ticker = normalizeTicker(raw);
  if (!isValidTicker(ticker)) {
    return { ok: false, reason: "invalid", limit: watchlistLimit(tier) };
  }
  if (current.includes(ticker)) {
    return { ok: false, reason: "duplicate", limit: watchlistLimit(tier) };
  }
  const limit = watchlistLimit(tier);
  if (limit !== null && current.length >= limit) {
    return { ok: false, reason: "limit", limit };
  }
  return { ok: true, tickers: [...current, ticker] };
}

export interface WatchlistRow {
  ticker: string;
  name: string;
  latestSignal: PublicSignal | null;
  alphaScore: number | null;
  scoreDelta: number | null;
  rateSensitivity: Sensitivity | null;
}

function isNewerSignal(candidate: PublicSignal, current: PublicSignal): boolean {
  if ((candidate.publishedAt ?? "") !== (current.publishedAt ?? "")) {
    return (candidate.publishedAt ?? "") > (current.publishedAt ?? "");
  }
  return candidate.signalId > current.signalId;
}

/**
 * One row per watchlisted ticker, enriched exactly like the Screener: the
 * latest published signal supplies direction/strength/sensitivity, the latest
 * approved weekly alert supplies Alpha Score and the weekly delta. A ticker
 * that left the covered universe still renders (name falls back to the ticker).
 */
export function buildWatchlistRows(
  tickers: string[],
  companies: PublicCompany[],
  signals: PublicSignal[],
  alerts: PublicAlert[],
): WatchlistRow[] {
  const companyByTicker = new Map(companies.map((company) => [company.ticker, company]));

  const latestSignalByTicker = new Map<string, PublicSignal>();
  for (const signal of signals) {
    for (const ticker of signal.tickers) {
      const current = latestSignalByTicker.get(ticker);
      if (!current || isNewerSignal(signal, current)) {
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

  return tickers.map((ticker) => {
    const latestSignal = latestSignalByTicker.get(ticker) ?? null;
    const latestAlert = latestAlertByTicker.get(ticker) ?? null;
    return {
      ticker,
      name: companyByTicker.get(ticker)?.name ?? ticker,
      latestSignal,
      alphaScore: latestAlert?.alphaScore ?? null,
      scoreDelta: latestAlert?.scoreDelta ?? null,
      rateSensitivity: latestSignal?.macroOverlay.rateSensitivity ?? null,
    };
  });
}

/** Label for an alert channel, localized. */
export function alertChannelLabel(channel: AlertChannel, t: Messages["watchlist"]): string {
  return channel === "email" ? t.channelEmail : t.channelInApp;
}

/** Stored alert settings for one ticker (may cover several channels). */
export function settingsForTicker(settings: AlertSetting[], ticker: string): AlertSetting[] {
  return settings.filter((setting) => setting.ticker === ticker);
}

/** The setting the per-row form pre-fills: first stored row, else in-app on. */
export function preferredSetting(settings: AlertSetting[], ticker: string): AlertSetting {
  return settingsForTicker(settings, ticker)[0] ?? { ticker, channel: "in_app", enabled: true };
}
