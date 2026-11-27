/**
 * Watchlist (V2.1 §7.2): pure limit/add logic, the fixture store boundary,
 * and the page render (empty / list / at-limit states) against fixtures —
 * no live Supabase, zero network.
 */
process.env.ALPHASTOCK_DATA_SOURCE = "fixture";
delete process.env.ALPHASTOCK_USER_TIER;

import type { ReactElement } from "react";
import { renderToString } from "react-dom/server";
import { afterAll, afterEach, describe, expect, it, vi } from "vitest";

// Actions revalidate outside any request scope in unit tests — a no-op here.
vi.mock("next/cache", () => ({ revalidatePath: () => undefined }));

import WatchlistPage from "../app/watchlist/page";
import {
  addTickerAction,
  removeTickerAction,
  updateAlertSettingAction,
} from "../app/watchlist/actions";
import companiesFixture from "../fixtures/companies.json";
import alertHistoryFixture from "../fixtures/alert-history.json";
import signalsFixture from "../fixtures/signals.json";
import { toPublicAlert, toPublicCompany, toPublicSignal } from "../lib/view";
import {
  applyAdd,
  buildWatchlistRows,
  currentTier,
  normalizeTicker,
  preferredSetting,
  settingsForTicker,
  watchlistLimit,
} from "../lib/watchlist";
import {
  activeWatchlistStore,
  resetFixtureWatchlist,
} from "../lib/watchlist-store";

const companies = (companiesFixture as unknown[]).map(toPublicCompany);
const signals = (signalsFixture as unknown[]).map(toPublicSignal);
const alerts = (alertHistoryFixture as unknown[]).map(toPublicAlert);

afterEach(resetFixtureWatchlist);

afterAll(() => {
  delete process.env.ALPHASTOCK_USER_TIER;
});

describe("tier limits (V2.1 spec: Explorer 5 / Pro 30 / Pro Plus 100 / Institutional unlimited)", () => {
  it("caps each tier", () => {
    expect(watchlistLimit("explorer")).toBe(5);
    expect(watchlistLimit("pro")).toBe(30);
    expect(watchlistLimit("pro_plus")).toBe(100);
    expect(watchlistLimit("institutional")).toBeNull();
  });

  it("defaults signed-in users to the Pro placeholder tier", () => {
    expect(currentTier()).toBe("pro");
    process.env.ALPHASTOCK_USER_TIER = "explorer";
    expect(currentTier()).toBe("explorer");
    process.env.ALPHASTOCK_USER_TIER = "nonsense";
    expect(currentTier()).toBe("pro");
    delete process.env.ALPHASTOCK_USER_TIER;
  });
});

describe("applyAdd (pure)", () => {
  it("normalizes case and whitespace", () => {
    expect(normalizeTicker(" vst ")).toBe("VST");
    const outcome = applyAdd([], "  gev ", "pro");
    expect(outcome).toEqual({ ok: true, tickers: ["GEV"] });
  });

  it("rejects tickers that are not 1–6 letters", () => {
    expect(applyAdd([], "TOOLONG1", "pro")).toMatchObject({ ok: false, reason: "invalid" });
    expect(applyAdd([], "", "pro")).toMatchObject({ ok: false, reason: "invalid" });
  });

  it("rejects duplicates case-insensitively", () => {
    expect(applyAdd(["VST"], "vst", "pro")).toMatchObject({ ok: false, reason: "duplicate" });
  });

  it("rejects adds at the tier limit, reports the limit", () => {
    const full = ["A", "B", "C", "D", "E"];
    expect(applyAdd(full, "F", "explorer")).toMatchObject({ ok: false, reason: "limit", limit: 5 });
    // Same list under Pro is not at the limit yet.
    expect(applyAdd(full, "F", "pro")).toMatchObject({ ok: true });
  });

  it("never hits a limit on the unlimited tier", () => {
    expect(watchlistLimit("institutional")).toBeNull();
  });
});

describe("watchlist rows", () => {
  it("enriches each ticker with the latest signal and latest approved alert", () => {
    const rows = buildWatchlistRows(["VST", "CEG", "ZZZZ"], companies, signals, alerts);
    expect(rows.map((row) => row.ticker)).toEqual(["VST", "CEG", "ZZZZ"]);
    for (const row of rows) {
      if (row.ticker === "ZZZZ") {
        // Not in the covered universe: name falls back to the ticker, no data.
        expect(row.name).toBe("ZZZZ");
        expect(row.latestSignal).toBeNull();
        expect(row.alphaScore).toBeNull();
      } else {
        expect(row.latestSignal).not.toBeNull();
        expect(row.rateSensitivity).toEqual(row.latestSignal!.macroOverlay.rateSensitivity);
      }
    }
    const vst = rows.find((row) => row.ticker === "VST")!;
    const vstAlert = alerts
      .filter((alert) => alert.ticker === "VST" && alert.approved)
      .sort((a, b) => (a.alertDate > b.alertDate ? 1 : -1))
      .at(-1)!;
    expect(vst.alphaScore).toBe(vstAlert.alphaScore);
    expect(vst.scoreDelta).toBe(vstAlert.scoreDelta);
  });

  it("ignores unapproved alerts for scores", () => {
    const unapproved = alerts.filter((alert) => !alert.approved).map((alert) => alert.ticker);
    for (const ticker of unapproved) {
      const rows = buildWatchlistRows([ticker], companies, signals, alerts);
      const approvedForTicker = alerts.some(
        (alert) => alert.ticker === ticker && alert.approved,
      );
      if (!approvedForTicker) expect(rows[0].alphaScore).toBeNull();
    }
  });
});

describe("fixture store (in-memory, zero network)", () => {
  it("persists adds, removes, and alert settings for the demo user", async () => {
    const store = await activeWatchlistStore();
    expect((await store.load()).tickers).toEqual([]);

    await store.add("VST");
    await store.add("CEG");
    await store.setAlert("VST", "in_app", false);
    await store.setAlert("VST", "email", true);

    const loaded = await store.load();
    expect(loaded.tickers).toEqual(["VST", "CEG"]);
    expect(settingsForTicker(loaded.settings, "VST")).toEqual([
      { ticker: "VST", channel: "in_app", enabled: false },
      { ticker: "VST", channel: "email", enabled: true },
    ]);

    // Toggle the same (ticker, channel) again — upsert, not a second row.
    await store.setAlert("VST", "in_app", true);
    expect(settingsForTicker((await store.load()).settings, "VST")).toHaveLength(2);

    await store.remove("VST");
    const afterRemove = await store.load();
    expect(afterRemove.tickers).toEqual(["CEG"]);
    // Removing a ticker clears its alert settings too.
    expect(settingsForTicker(afterRemove.settings, "VST")).toEqual([]);
  });

  it("load() returns defensive copies", async () => {
    const store = await activeWatchlistStore();
    await store.add("VST");
    const first = await store.load();
    first.tickers.push("GEV");
    expect((await store.load()).tickers).toEqual(["VST"]);
  });
});

describe("preferred alert setting", () => {
  it("pre-fills from the first stored row, defaulting to in-app on", () => {
    expect(preferredSetting([], "VST")).toEqual({
      ticker: "VST",
      channel: "in_app",
      enabled: true,
    });
    const settings = [
      { ticker: "VST", channel: "email" as const, enabled: false },
      { ticker: "VST", channel: "in_app" as const, enabled: true },
    ];
    expect(preferredSetting(settings, "VST").channel).toBe("email");
  });
});

describe("/watchlist page", () => {
  async function renderPage(): Promise<string> {
    return renderToString(await (WatchlistPage as unknown as () => Promise<ReactElement>)()).replace(
      /<!-- -->/g,
      "",
    );
  }

  it("renders the empty state with the add form, in English", async () => {
    const html = await renderPage();
    expect(html).toContain("Watchlist");
    expect(html).toContain("No tickers yet");
    expect(html).toContain("Add ticker");
    expect(html).not.toMatch(/[\u4e00-\u9fff]/);
  });

  it("renders watchlisted rows with latest signal and Alpha Score delta", async () => {
    const store = await activeWatchlistStore();
    await store.add("VST");
    await store.setAlert("VST", "email", true);
    const html = await renderPage();
    expect(html).toContain("data-ticker=\"VST\"");
    expect(html).toContain("Vistra Corp");
    expect(html).toMatch(/class="as-signal-id">SIG-/);
    // Alert settings render as a channel badge and pre-fill the form.
    expect(html).toContain("Email: ✓");
    expect(html).toMatch(/name="channel"[\s\S]*?value="email"/);
    expect(html).toContain("1 / 30 tickers");
  });

  it("surfaces the friendly at-limit state instead of the add form", async () => {
    process.env.ALPHASTOCK_USER_TIER = "explorer";
    const store = await activeWatchlistStore();
    for (const ticker of ["VST", "CEG", "GEV", "ETN", "PWR"]) await store.add(ticker);
    const html = await renderPage();
    expect(html).toContain("Watchlist is full — your plan allows 5 tickers.");
    expect(html).toContain("Upgrade your plan");
    expect(html).not.toContain("Add ticker");
    // The existing rows still render.
    expect(html).toContain("data-ticker=\"CEG\"");
    delete process.env.ALPHASTOCK_USER_TIER;
  });
});

describe("watchlist actions (fixture mode, no Next request scope needed)", () => {
  it("addTickerAction accepts and rejects through the pure rules", async () => {
    expect(await addTickerAction({ status: "idle" }, formData({ ticker: "vst" }))).toEqual({
      status: "idle",
    });
    expect((await activeWatchlistStore().then((store) => store.load())).tickers).toEqual(["VST"]);

    expect(await addTickerAction({ status: "idle" }, formData({ ticker: "VST" }))).toEqual({
      status: "duplicate",
    });
    expect(await addTickerAction({ status: "idle" }, formData({ ticker: "!!!" }))).toEqual({
      status: "invalid",
    });

    process.env.ALPHASTOCK_USER_TIER = "explorer";
    // The store already holds one ticker: fill to the 5-ticker cap, then hit it.
    for (const ticker of ["A", "B", "C", "D"]) {
      await addTickerAction({ status: "idle" }, formData({ ticker }));
    }
    expect(await addTickerAction({ status: "idle" }, formData({ ticker: "E" }))).toEqual({
      status: "limit",
    });
    expect((await activeWatchlistStore().then((store) => store.load())).tickers).toHaveLength(5);
    delete process.env.ALPHASTOCK_USER_TIER;
  });

  it("removeTickerAction removes by ticker", async () => {
    await addTickerAction({ status: "idle" }, formData({ ticker: "VST" }));
    await removeTickerAction(formData({ ticker: "VST" }));
    expect((await activeWatchlistStore().then((store) => store.load())).tickers).toEqual([]);
  });

  it("updateAlertSettingAction upserts channel + enabled", async () => {
    await addTickerAction({ status: "idle" }, formData({ ticker: "VST" }));
    await updateAlertSettingAction(
      formData({ ticker: "VST", channel: "email", enabled: "on" }),
    );
    expect(settingsForTicker((await activeWatchlistStore().then((store) => store.load())).settings, "VST")).toEqual([
      { ticker: "VST", channel: "email", enabled: true },
    ]);
    await updateAlertSettingAction(formData({ ticker: "VST", channel: "email" }));
    expect(settingsForTicker((await activeWatchlistStore().then((store) => store.load())).settings, "VST")).toEqual([
      { ticker: "VST", channel: "email", enabled: false },
    ]);
  });
});

function formData(fields: Record<string, string>): FormData {
  const data = new FormData();
  for (const [key, value] of Object.entries(fields)) data.set(key, value);
  return data;
}
