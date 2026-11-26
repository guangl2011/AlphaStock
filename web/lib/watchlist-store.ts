/**
 * Watchlist store boundary — the only file that knows where user rows live.
 *
 * Two implementations, chosen by the same mode switch as the data layer:
 *  - fixture: an in-memory store keyed by the demo identity. Zero network,
 *    module-level state shared by the page and the server actions in one
 *    server process (the documented V2.1 fixture contract).
 *  - supabase: per-user reads and writes through the Supabase server client.
 *    Every statement is filtered by user_id from auth.getUser() — defense in
 *    depth on top of the 0004 RLS policies, which scope rows to auth.uid().
 *    There is no global read path for user rows, here or anywhere else.
 */
import { FIXTURE_USER_EMAIL, authFixtureMode } from "./auth";
import { createSupabaseServerClient } from "./supabase/server";
import type { AlertChannel, AlertSetting } from "./watchlist";
import { normalizeTicker } from "./watchlist";

export type { AlertSetting } from "./watchlist";

export interface WatchlistData {
  /** Watchlisted tickers, oldest first. */
  tickers: string[];
  settings: AlertSetting[];
}

export interface WatchlistStore {
  load(): Promise<WatchlistData>;
  /** Persist a validated add (limit/duplicate checks happen in lib/watchlist.ts). */
  add(ticker: string): Promise<void>;
  remove(ticker: string): Promise<void>;
  setAlert(ticker: string, channel: AlertChannel, enabled: boolean): Promise<void>;
}

export async function activeWatchlistStore(): Promise<WatchlistStore> {
  return authFixtureMode() ? fixtureWatchlistStore() : supabaseWatchlistStore();
}

/* ------------------------------------------------------------------ */
/* Fixture mode: in-memory demo store                                  */
/* ------------------------------------------------------------------ */

interface FixtureWatchlist {
  tickers: string[];
  settings: AlertSetting[];
}

const fixtureWatchlists = new Map<string, FixtureWatchlist>([
  [FIXTURE_USER_EMAIL, { tickers: [], settings: [] }],
]);

/** Test/dogfood hook: restore the demo store to its pristine empty state. */
export function resetFixtureWatchlist(): void {
  fixtureWatchlists.set(FIXTURE_USER_EMAIL, { tickers: [], settings: [] });
}

function fixtureWatchlistStore(): WatchlistStore {
  const state = () => fixtureWatchlists.get(FIXTURE_USER_EMAIL)!;

  return {
    async load(): Promise<WatchlistData> {
      const watchlist = state();
      return {
        tickers: [...watchlist.tickers],
        settings: watchlist.settings.map((setting) => ({ ...setting })),
      };
    },

    async add(ticker): Promise<void> {
      state().tickers.push(ticker);
    },

    async remove(ticker): Promise<void> {
      const watchlist = state();
      watchlist.tickers = watchlist.tickers.filter((t) => t !== ticker);
      watchlist.settings = watchlist.settings.filter((setting) => setting.ticker !== ticker);
    },

    async setAlert(ticker, channel, enabled): Promise<void> {
      const watchlist = state();
      const existing = watchlist.settings.find(
        (setting) => setting.ticker === ticker && setting.channel === channel,
      );
      if (existing) {
        existing.enabled = enabled;
      } else {
        watchlist.settings.push({ ticker, channel, enabled });
      }
    },
  };
}

/* ------------------------------------------------------------------ */
/* Supabase mode: per-user queries against the 0004 tables             */
/* ------------------------------------------------------------------ */

function storeError(operation: string, error: { message: string; code?: string }): Error {
  return new Error(`user_watchlists ${operation} failed (${error.code ?? "unknown"}): ${error.message}`);
}

function supabaseWatchlistStore(): WatchlistStore {
  return {
    async load(): Promise<WatchlistData> {
      const supabase = await createSupabaseServerClient();
      const user = await requireUser(supabase);

      const [watchlistRows, settingRows] = await Promise.all([
        supabase
          .from("user_watchlists")
          .select("ticker, added_at")
          .eq("user_id", user.id)
          .order("added_at", { ascending: true }),
        supabase
          .from("user_alert_settings")
          .select("ticker, channel, enabled")
          .eq("user_id", user.id)
          .order("ticker", { ascending: true }),
      ]);

      if (watchlistRows.error) throw storeError("select", watchlistRows.error);
      if (settingRows.error) throw storeError("select", settingRows.error);

      return {
        tickers: watchlistRows.data.map((row) => normalizeTicker(row.ticker)),
        settings: settingRows.data.map((row) => ({
          ticker: normalizeTicker(row.ticker),
          channel: row.channel as AlertChannel,
          enabled: row.enabled === true,
        })),
      };
    },

    async add(ticker): Promise<void> {
      const supabase = await createSupabaseServerClient();
      const user = await requireUser(supabase);
      const { error } = await supabase
        .from("user_watchlists")
        .insert({ user_id: user.id, ticker });
      if (error) throw storeError("insert", error);
    },

    async remove(ticker): Promise<void> {
      const supabase = await createSupabaseServerClient();
      const user = await requireUser(supabase);
      const { error } = await supabase
        .from("user_watchlists")
        .delete()
        .eq("user_id", user.id)
        .eq("ticker", ticker);
      if (error) throw storeError("delete", error);
    },

    async setAlert(ticker, channel, enabled): Promise<void> {
      const supabase = await createSupabaseServerClient();
      const user = await requireUser(supabase);
      const { error } = await supabase
        .from("user_alert_settings")
        .upsert(
          { user_id: user.id, ticker, channel, enabled },
          { onConflict: "user_id,ticker,channel" },
        );
      if (error) throw storeError("upsert", error);
    },
  };
}

/** Session-less Supabase client means auth is misconfigured — fail loudly, never fake. */
type SupabaseAuthClient = Awaited<ReturnType<typeof createSupabaseServerClient>>;

async function requireUser(supabase: SupabaseAuthClient): Promise<{ id: string }> {
  const { data, error } = await supabase.auth.getUser();
  if (error || !data.user) {
    throw new Error("watchlist requires an authenticated session (auth.getUser() returned none)");
  }
  return { id: data.user.id };
}
