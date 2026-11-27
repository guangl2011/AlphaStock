/**
 * Portfolio fixture-store tests — the in-memory fake-user store must behave
 * like the RLS-scoped user_portfolios table: per-user isolation, upsert by
 * ticker, remove. Zero network in fixture mode.
 */
process.env.ALPHASTOCK_DATA_SOURCE = "fixture";

import { beforeEach, describe, expect, it } from "vitest";

import { FIXTURE_USER_EMAIL } from "../lib/auth";
import {
  getPortfolio,
  removeHolding,
  resetPortfolioFixtureStore,
  resolvePortfolioUserId,
  upsertHoldings,
} from "../lib/portfolio-store";

const OTHER_USER = "someone-else@example.com";

beforeEach(() => {
  resetPortfolioFixtureStore();
});

describe("fixture portfolio store", () => {
  it("resolves the demo identity in fixture mode", async () => {
    expect(await resolvePortfolioUserId()).toBe(FIXTURE_USER_EMAIL);
  });

  it("seeds the demo user with sample holdings on first read", async () => {
    const holdings = await getPortfolio(FIXTURE_USER_EMAIL);
    expect(holdings.map((h) => h.ticker)).toEqual(["NEE", "VST"]);
  });

  it("keeps users isolated — another user starts empty", async () => {
    await upsertHoldings(FIXTURE_USER_EMAIL, [
      { ticker: "ETN", shares: 3, costBasis: null },
    ]);
    expect(await getPortfolio(OTHER_USER)).toEqual([]);
    expect((await getPortfolio(FIXTURE_USER_EMAIL)).map((h) => h.ticker)).toContain("ETN");
  });

  it("upserts by ticker — a second write replaces the row", async () => {
    await upsertHoldings(FIXTURE_USER_EMAIL, [
      { ticker: "VST", shares: 200, costBasis: 30 },
    ]);
    const holdings = await getPortfolio(FIXTURE_USER_EMAIL);
    const vst = holdings.find((h) => h.ticker === "VST");
    expect(vst).toEqual({ ticker: "VST", shares: 200, costBasis: 30 });
    expect(holdings).toHaveLength(2); // no duplicate row
  });

  it("removes one holding without touching the rest", async () => {
    await removeHolding(FIXTURE_USER_EMAIL, "VST");
    const holdings = await getPortfolio(FIXTURE_USER_EMAIL);
    expect(holdings.map((h) => h.ticker)).toEqual(["NEE"]);
  });

  it("re-seeds the demo identity on the first read after a reset", async () => {
    await upsertHoldings(FIXTURE_USER_EMAIL, [
      { ticker: "ETN", shares: 3, costBasis: null },
    ]);
    resetPortfolioFixtureStore();
    // Reset clears everything; the next read re-seeds the demo holdings.
    expect((await getPortfolio(FIXTURE_USER_EMAIL)).map((h) => h.ticker)).toEqual([
      "NEE",
      "VST",
    ]);
  });
});
