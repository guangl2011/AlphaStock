/**
 * The compliance wall: lib/view.ts mappers are the only path from Postgres
 * rows to renderable models, so the display rules hold if (and only if) the
 * mappers strip the paid-tier fields. These tests pin that behavior.
 */
process.env.ALPHASTOCK_DATA_SOURCE = "fixture";

import { describe, expect, it } from "vitest";

import signalsFixture from "../fixtures/signals.json";
import { RowParseError, toPublicSignal } from "../lib/view";

const baseRow = (signalsFixture as Array<Record<string, unknown>>)[0];

describe("toPublicSignal", () => {
  it("maps snake_case columns to the camelCase view model", () => {
    const signal = toPublicSignal(baseRow);
    expect(signal.signalId).toBe(baseRow.signal_id);
    expect(signal.eventType).toBe("status_transition");
    expect(signal.summaryEn).toBe(baseRow.summary_en);
    expect(signal.summaryZh).toBe(baseRow.summary_zh);
    expect(signal.publishedAt).toBe(baseRow.published_at);
    expect(signal.strength).toBe(4);
  });

  it("drops the numeric scenario range and probability edge entirely", () => {
    const signal = toPublicSignal(baseRow);
    expect("scenario_range" in signal).toBe(false);
    expect("scenarioRange" in signal).toBe(false);
    expect("probability_edge" in signal).toBe(false);
    expect("probabilityEdge" in signal).toBe(false);
    expect(JSON.stringify(signal)).not.toContain("0.61");
    expect(JSON.stringify(signal)).not.toContain("bear");
  });

  it("derives a qualitative probability band from the numeric edge", () => {
    expect(toPublicSignal(baseRow).probabilityBand).toBe("slight positive");
    const row = { ...baseRow, probability_edge: 0.2 };
    expect(toPublicSignal(row).probabilityBand).toBe("strong negative");
    const row2 = { ...baseRow, probability_edge: null };
    expect(toPublicSignal(row2).probabilityBand).toBe("balanced");
  });

  it("parses sources into labeled links with retrieval dates", () => {
    const signal = toPublicSignal(baseRow);
    expect(signal.sources).toHaveLength(1);
    expect(signal.sources[0]).toMatchObject({
      label: "ISO_queue",
      url: "https://www.ercot.com/mktrules/guides/transmission",
      retrievedAt: "2026-11-08T09:15:00Z",
    });
  });

  it("parses the macro overlay into qualitative sensitivities", () => {
    const signal = toPublicSignal(baseRow);
    expect(signal.macroOverlay).toEqual({ rateSensitivity: "medium", usdSensitivity: "low" });
  });

  it("rejects malformed rows loudly instead of rendering them", () => {
    expect(() => toPublicSignal({ ...baseRow, strength: 9 })).toThrow(RowParseError);
    expect(() => toPublicSignal({ ...baseRow, direction: "up" })).toThrow(RowParseError);
    expect(() => toPublicSignal({ ...baseRow, hash: "" })).toThrow(RowParseError);
    expect(() =>
      toPublicSignal({ ...baseRow, sources: [{ url: "not-a-url" }] }),
    ).toThrow(RowParseError);
  });
});
