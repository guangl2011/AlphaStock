/** /factors library against the committed correlation-model export. */
process.env.ALPHASTOCK_DATA_SOURCE = "fixture";

import type { ReactElement } from "react";
import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";

import FactorsPage from "../app/factors/page";
import factorsFixture from "../fixtures/factors.json";
import { correlationOf, fmt, heatColor, parseFactorsFixture } from "../lib/factors";

describe("factors fixture", () => {
  const data = parseFactorsFixture(factorsFixture);

  it("covers three indices and eight factors", () => {
    expect(data.indexIds).toEqual(["AS-100", "AS-MCB100", "AS-MCG100"]);
    expect(data.catalog.map((c) => c.factor)).toEqual([
      "BREADTH", "GRID", "MICRO", "MKT", "NUKE", "ROBO", "SIZE", "UTIL",
    ]);
    expect(data.catalog.filter((c) => c.inRegression).map((c) => c.factor).sort()).toEqual([
      "BREADTH", "GRID", "MKT", "NUKE", "ROBO", "UTIL",
    ]);
  });

  it("keeps correlation-only factors without regression statistics", () => {
    for (const id of data.indexIds) {
      expect(data.exposures[id].factors.SIZE.beta).toBeNull();
      expect(data.exposures[id].factors.MKT.beta).not.toBeNull();
    }
  });

  it("looks up the symmetric matrix and pins the diagonal at 1", () => {
    const full = data.correlations.full;
    expect(correlationOf(full, "AS-MCG100", "AS-MCB100")).toBe(correlationOf(full, "AS-MCB100", "AS-MCG100"));
    expect(correlationOf(full, "MKT", "MKT")).toBe(1);
  });
});

describe("helpers", () => {
  it("formats empty as empty, never zero", () => {
    expect(fmt(null)).toBe("");
    expect(fmt(-0.4567)).toBe("−0.46");
  });
  it("heat colors are diverging and transparent when undefined", () => {
    expect(heatColor(0.9)).toContain("42, 120, 214");
    expect(heatColor(-0.9)).toContain("235, 104, 52");
    expect(heatColor(null)).toBe("transparent");
  });
});

describe("/factors page (English)", () => {
  it("renders catalog, exposures, matrix and leaders", async () => {
    const page = FactorsPage as unknown as () => Promise<ReactElement>;
    const html = renderToString(await page()).replace(/<!-- -->/g, "");
    expect(html).toContain("Factors library");
    expect(html).toContain("Factor catalog");
    expect(html).toContain("IWM − SPY");
    expect(html).toContain("Index factor exposures");
    expect(html).toContain("Correlation matrix (full window)");
    expect(html).toContain("Stocks that load hardest on each factor");
    expect(html).toContain("UUUU");
    expect(html).not.toMatch(/[一-鿿]/);
  });
});
