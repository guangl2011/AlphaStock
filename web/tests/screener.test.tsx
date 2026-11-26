/**
 * Screener (V2.1 §7.2) — filter semantics for all five condition types, the
 * empty state, and the CSV-export contract. Every expected count is derived
 * from the fixture the page itself loads, never hardcoded.
 */
process.env.ALPHASTOCK_DATA_SOURCE = "fixture";

import type { ReactElement } from "react";
import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";

import ScreenerPage from "../app/screener/page";
import { GET as exportRoute } from "../app/screener/export/route";
import alertsFixture from "../fixtures/alert-history.json";
import companiesFixture from "../fixtures/companies.json";
import { MESSAGES } from "../lib/messages";
import {
  applyScreenerFilters,
  buildScreenerRows,
  parseScreenerFilters,
  sortScreenerRows,
  toScreenerCsv,
} from "../lib/screener";
import { getScreenerData } from "../lib/data";

async function renderPage(params: Record<string, string>): Promise<string> {
  const page = ScreenerPage as (p: never) => Promise<ReactElement>;
  const html = renderToString(
    await page({ searchParams: Promise.resolve(params) } as never),
  );
  // React separates adjacent text nodes with comment markers in SSR output.
  return html.replace(/<!-- -->/g, "");
}

/** Rendered result rows, in order, keyed by their data-ticker attribute. */
function renderedRows(html: string): string[] {
  return [...html.matchAll(/<tr data-ticker="([^"]+)"/g)].map((match) => match[1]);
}

async function screenerData() {
  return getScreenerData();
}

function filterAndSort(params: Record<string, string>) {
  const filters = parseScreenerFilters(params);
  return sortScreenerRows(
    applyScreenerFilters(
      buildScreenerRows(screener.companies, screener.signals, screener.alerts),
      filters,
    ),
  );
}

// Shared fixture-mode data — parsed through the same mappers the page uses.
const screener = await screenerData();

describe("screener filters", () => {
  it("renders the whole covered universe unfiltered", async () => {
    const html = await renderPage({});
    expect(renderedRows(html)).toHaveLength(companiesFixture.length);
    expect(html).toContain("Screener");
    expect(html).toContain("Export CSV");
  });

  it("filters by theme", async () => {
    const expected = companiesFixture
      .filter((company) => (company.themes as string[]).includes("Data Centers"))
      .map((company) => company.ticker)
      .sort();
    const html = await renderPage({ theme: "Data Centers" });
    expect(renderedRows(html)).toEqual(
      filterAndSort({ theme: "Data Centers" }).map((row) => row.ticker),
    );
    expect(filterAndSort({ theme: "Data Centers" }).map((row) => row.ticker).sort()).toEqual(expected);
  });

  it("filters by category of the latest signal", async () => {
    const rows = filterAndSort({ category: "V2_INTERCONNECTION" });
    expect(rows.length).toBeGreaterThan(0);
    for (const row of rows) {
      expect(row.latestSignal?.category).toBe("V2_INTERCONNECTION");
    }
    const html = await renderPage({ category: "V2_INTERCONNECTION" });
    expect(renderedRows(html)).toEqual(rows.map((row) => row.ticker));
  });

  it("filters by the strength range of the latest signal", async () => {
    const rows = filterAndSort({ minStrength: "3", maxStrength: "5" });
    expect(rows.length).toBeGreaterThan(0);
    for (const row of rows) {
      expect(row.latestSignal?.strength ?? 0).toBeGreaterThanOrEqual(3);
      expect(row.latestSignal?.strength ?? 0).toBeLessThanOrEqual(5);
    }
    const html = await renderPage({ minStrength: "3", maxStrength: "5" });
    expect(renderedRows(html)).toEqual(rows.map((row) => row.ticker));
  });

  it("filters by Alpha Score range and drops unscored names", async () => {
    const rows = filterAndSort({ minScore: "65" });
    const approvedScores = (alertsFixture as Array<{ approved: boolean; alpha_score: number }>)
      .filter((alert) => alert.approved && alert.alpha_score >= 65).length;
    expect(rows.length).toBe(approvedScores);
    for (const row of rows) {
      expect(row.alphaScore ?? 0).toBeGreaterThanOrEqual(65);
    }
    const html = await renderPage({ minScore: "65" });
    expect(renderedRows(html)).toEqual(rows.map((row) => row.ticker));
  });

  it("filters by rate-sensitivity tier", async () => {
    const rows = filterAndSort({ sensitivity: "high" });
    expect(rows.length).toBeGreaterThan(0);
    for (const row of rows) {
      expect(row.rateSensitivity).toBe("high");
    }
    const html = await renderPage({ sensitivity: "high" });
    expect(renderedRows(html)).toEqual(rows.map((row) => row.ticker));
  });

  it("treats empty GET-form submissions as unset filters", () => {
    // Browsers submit every form field; empty strings must not become 0.
    const filters = parseScreenerFilters({
      theme: "",
      category: "",
      minStrength: "",
      maxStrength: "",
      minScore: "",
      maxScore: "",
      sensitivity: "",
    });
    expect(filters).toEqual({});
    const all = filterAndSort({ minScore: "", maxScore: "" });
    expect(all).toHaveLength(companiesFixture.length);
  });

  it("shows an explicit empty state that suggests relaxing filters", async () => {
    const html = await renderPage({ theme: "Utilities", minStrength: "5" });
    expect(html).toContain("No companies match these filters");
    expect(html).toContain("try relaxing the score range");
    expect(renderedRows(html)).toHaveLength(0);
  });
});

describe("screener CSV export", () => {
  async function exportCsv(query: string): Promise<string> {
    const response = await exportRoute(
      new Request(`http://localhost:3000/screener/export?${query}`),
    );
    expect(response.headers.get("Content-Type")).toContain("text/csv");
    expect(response.headers.get("Content-Disposition")).toContain("attachment");
    return response.text();
  }

  it("matches the rendered table row for row, in the same order", async () => {
    const params = { sensitivity: "high" };
    const html = await renderPage(params);
    const csv = await exportCsv("sensitivity=high");

    const tableTickers = renderedRows(html);
    const lines = csv.replace(/\r\n$/, "").split("\r\n");
    expect(lines[0]).toBe(
      '"Ticker","Company","Themes","Category","Latest signal","Direction","Strength","Alpha Score","Delta","Rate sensitivity"',
    );

    const csvRows = lines.slice(1).map((line) =>
      line
        .slice(1, -1)
        .split('","')
        .map((cell) => cell.replace(/""/g, '"')),
    );
    expect(csvRows.map((cells) => cells[0])).toEqual(tableTickers);

    // Each data line carries the same name, signal id, and score as its row.
    for (const cells of csvRows) {
      const [ticker, name, , , signalId, , strength, score] = cells;
      const rowHtml = html.match(new RegExp(`<tr data-ticker="${ticker}"[\\s\\S]*?</tr>`))![0];
      expect(rowHtml).toContain(name);
      if (signalId) {
        expect(rowHtml).toContain(signalId);
        expect(rowHtml).toContain("●".repeat(Number(strength)));
        expect(rowHtml).toContain(`>${score}<`);
      }
    }
  });

  it("exports the unfiltered universe when no filters are set", async () => {
    const csv = await exportCsv("");
    const lines = csv.replace(/\r\n$/, "").split("\r\n");
    expect(lines).toHaveLength(companiesFixture.length + 1);
  });
});

describe("screener i18n", () => {
  it("renders zh labels for filters and sensitivity tiers, one language at a time", async () => {
    const t = MESSAGES.zh;
    expect(t.screener.title).toBe("筛选器");
    expect(t.screener.sensitivity.high).toBe("高");
    const csv = toScreenerCsv(
      filterAndSort({ sensitivity: "high" }),
      MESSAGES.zh,
      "zh",
    );
    expect(csv).toContain('"高"');
    expect(csv).not.toContain('"High"');
  });
});
