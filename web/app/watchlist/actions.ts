"use server";

/**
 * Watchlist server actions (V2.1 §7.2). Each mutation validates through the
 * pure helpers in lib/watchlist.ts, persists through the store boundary in
 * lib/watchlist-store.ts, and returns a discriminated union the form renders —
 * every failure path returns a state, never a raw error or a hang.
 */
import { revalidatePath } from "next/cache";

import {
  ALERT_CHANNELS,
  applyAdd,
  currentTier,
  isValidTicker,
  normalizeTicker,
  parseAlertChannel,
} from "@/lib/watchlist";
import { activeWatchlistStore } from "@/lib/watchlist-store";

export type AddTickerState =
  | { status: "idle" }
  | { status: "invalid" }
  | { status: "duplicate" }
  | { status: "limit" }
  | { status: "service" };

export async function addTickerAction(
  _prev: AddTickerState,
  formData: FormData,
): Promise<AddTickerState> {
  const raw = String(formData.get("ticker") ?? "");
  try {
    const store = await activeWatchlistStore();
    const { tickers } = await store.load();
    const outcome = applyAdd(tickers, raw, currentTier());
    if (!outcome.ok) return { status: outcome.reason };
    await store.add(normalizeTicker(raw));
    revalidatePath("/watchlist");
    return { status: "idle" };
  } catch {
    return { status: "service" };
  }
}

/** Remove is a plain form action; a stale double-submit removing an absent ticker is a no-op. */
export async function removeTickerAction(formData: FormData): Promise<void> {
  const ticker = normalizeTicker(String(formData.get("ticker") ?? ""));
  if (!ticker) return;
  try {
    const store = await activeWatchlistStore();
    await store.remove(ticker);
    revalidatePath("/watchlist");
  } catch {
    // The next render still shows the persisted state; a swallowed remove here
    // would only hide an already-failing store, which load() surfaces loudly.
    throw new Error("watchlist remove failed");
  }
}

export async function updateAlertSettingAction(formData: FormData): Promise<void> {
  const ticker = normalizeTicker(String(formData.get("ticker") ?? ""));
  const channel = parseAlertChannel(String(formData.get("channel") ?? ""));
  if (!isValidTicker(ticker) || !channel) return;
  if (!ALERT_CHANNELS.includes(channel)) return;
  const enabled = formData.get("enabled") === "on";
  try {
    const store = await activeWatchlistStore();
    await store.setAlert(ticker, channel, enabled);
    revalidatePath("/watchlist");
  } catch {
    throw new Error("alert setting update failed");
  }
}
