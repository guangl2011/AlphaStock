/**
 * Portfolio data access — user-scoped by construction (design doc v2.1).
 *
 * Two modes, mirroring lib/data.ts:
 *  - "supabase": reads and writes against user_portfolios (migration 0004)
 *    through the authenticated Supabase server client. Every query filters on
 *    user_id resolved from the session (auth.uid()) — RLS enforces the same
 *    bound at the database, the explicit filter keeps the intent visible and
 *    means no code path can read another user's rows even in tests.
 *  - "fixture": an in-memory store keyed by the demo identity. Zero network,
 *    zero Supabase configuration — CI, local dev, and e2e are deterministic.
 *
 * There is no anon path here: user rows never touch the public read layer.
 */
import { FIXTURE_USER_EMAIL, authFixtureMode } from "./auth";
import { createSupabaseServerClient } from "./supabase/server";
import type { PortfolioHolding } from "./portfolio";

export interface PortfolioRecord {
  ticker: string;
  shares: number;
  costBasis: number | null;
}

/** Resolved principal for user-scoped portfolio access. */
export async function resolvePortfolioUserId(): Promise<string> {
  if (authFixtureMode()) return FIXTURE_USER_EMAIL;
  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase.auth.getUser();
  if (error || !data.user) {
    throw new Error("Portfolio requires an authenticated session");
  }
  return data.user.id;
}

// ---------------------------------------------------------------------------
// Fixture mode — in-memory, seeded demo holdings, zero network
// ---------------------------------------------------------------------------

/** Demo holdings: both tickers exist in fixture signals with rate sensitivity. */
const FIXTURE_SEED: PortfolioRecord[] = [
  { ticker: "VST", shares: 100, costBasis: 25 },
  { ticker: "NEE", shares: 50, costBasis: null },
];

const fixtureStore = new Map<string, Map<string, PortfolioRecord>>();

function fixtureUserStore(userId: string): Map<string, PortfolioRecord> {
  let store = fixtureStore.get(userId);
  if (!store) {
    store = new Map();
    fixtureStore.set(userId, store);
  }
  return store;
}

/** Seed the demo identity on first access, not at import time, so tests can reset. */
function ensureFixtureSeeded(userId: string): Map<string, PortfolioRecord> {
  const store = fixtureUserStore(userId);
  if (userId === FIXTURE_USER_EMAIL && store.size === 0 && !fixtureSeeded) {
    for (const record of FIXTURE_SEED) store.set(record.ticker, record);
    fixtureSeeded = true;
  }
  return store;
}

let fixtureSeeded = false;

/** Test hook: return the fixture store to its unseeded state. */
export function resetPortfolioFixtureStore(): void {
  fixtureStore.clear();
  fixtureSeeded = false;
}

// ---------------------------------------------------------------------------
// Public API — same three operations in both modes
// ---------------------------------------------------------------------------

/** The user's holdings, ticker-ascending. Only the owner's rows are read. */
export async function getPortfolio(userId: string): Promise<PortfolioRecord[]> {
  if (authFixtureMode()) {
    const store = ensureFixtureSeeded(userId);
    return [...store.values()].sort((a, b) => (a.ticker > b.ticker ? 1 : -1));
  }

  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase
    .from("user_portfolios")
    .select("ticker, shares, cost_basis")
    .eq("user_id", userId)
    .order("ticker");
  if (error) throw new Error(`portfolio read failed: ${error.message}`);
  return (data ?? []).map((row) => ({
    ticker: row.ticker as string,
    shares: Number(row.shares),
    costBasis: row.cost_basis === null ? null : Number(row.cost_basis),
  }));
}

/** Insert or update one holding per ticker (PK user_id + ticker). */
export async function upsertHoldings(
  userId: string,
  holdings: PortfolioHolding[],
): Promise<void> {
  if (holdings.length === 0) return;

  if (authFixtureMode()) {
    const store = ensureFixtureSeeded(userId);
    for (const holding of holdings) {
      store.set(holding.ticker, {
        ticker: holding.ticker,
        shares: holding.shares,
        costBasis: holding.costBasis,
      });
    }
    return;
  }

  const supabase = await createSupabaseServerClient();
  const { error } = await supabase.from("user_portfolios").upsert(
    holdings.map((holding) => ({
      user_id: userId,
      ticker: holding.ticker,
      shares: holding.shares,
      cost_basis: holding.costBasis,
    })),
  );
  if (error) throw new Error(`portfolio write failed: ${error.message}`);
}

/** Remove one holding. Deleting a row that does not exist is not an error. */
export async function removeHolding(userId: string, ticker: string): Promise<void> {
  if (authFixtureMode()) {
    ensureFixtureSeeded(userId).delete(ticker);
    return;
  }

  const supabase = await createSupabaseServerClient();
  const { error } = await supabase
    .from("user_portfolios")
    .delete()
    .eq("user_id", userId)
    .eq("ticker", ticker);
  if (error) throw new Error(`portfolio delete failed: ${error.message}`);
}
