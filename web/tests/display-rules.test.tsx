/**
 * Free-tier display-rule tests (design doc §6, enforced by view.ts):
 * scenario-range numbers and numeric probabilities must never reach the
 * rendered HTML, while sources and invalidation conditions must always be
 * visible. These tests render real components with server rendering, so the
 * assertions see exactly the markup a user would get.
 */
process.env.ALPHASTOCK_DATA_SOURCE = "fixture";

import type { ReactElement } from "react";
import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";

import signalsFixture from "../fixtures/signals.json";
import { Provenance } from "../components/provenance";
import { SignalCard } from "../components/signal-card";
import { MESSAGES } from "../lib/messages";
import { THEMES } from "../lib/themes";
import { toPublicSignal } from "../lib/view";

const t = MESSAGES.en;

const signals = (signalsFixture as unknown[]).map(toPublicSignal);

const loudoun = signals.find((s) => s.signalId === "SIG-2026-11-000121");
if (!loudoun) throw new Error("fixture signal SIG-2026-11-000121 missing");

/** Render to HTML, stripping React's `<!-- -->` text-node separators. */
function render(element: ReactElement): string {
  return renderToString(element).replace(/<!-- -->/g, "");
}

/** Visible text only — attributes (styles, aria-labels) are not user-facing. */
function visibleText(html: string): string {
  return html.replace(/<[^>]+>/g, " ");
}

/** Every internal modeling number in the fixtures, as a plain string. */
function internalNumbers(): string[] {
  const numbers: string[] = [];
  for (const raw of signalsFixture as Array<Record<string, unknown>>) {
    const range = raw.scenario_range as Record<string, number> | null;
    if (range) numbers.push(...Object.values(range).map(String));
    if (typeof raw.probability_edge === "number") numbers.push(String(raw.probability_edge));
  }
  return numbers;
}

describe("SignalCard display rules", () => {
  it("renders one language: the EN summary in English, the ZH summary in 中文", () => {
    const en = render(<SignalCard signal={loudoun} t={t} locale="en" />);
    expect(en).toContain("Loudoun County");
    expect(en).not.toContain("劳登县");
    expect(en).toContain("verified ✓");
    const zh = render(<SignalCard signal={loudoun} t={MESSAGES.zh} locale="zh" />);
    expect(zh).toContain("劳登县");
    expect(zh).not.toContain("Loudoun County data center");
    expect(zh).toContain("已核验 ✓");
    expect(zh).toContain("失效条件");
  });

  it("always shows the invalidation condition and source links", () => {
    const html = render(<SignalCard signal={loudoun} t={t} locale="en" />);
    expect(html).toContain("Invalidation condition");
    expect(html).toContain("Board of Supervisors reversal");
    expect(html).toContain('href="https://propertyweb.loudoun.gov/dataminer/"');
  });

  it("exposes strength and direction as badges, not numbers pretending to be advice", () => {
    const html = render(<SignalCard signal={loudoun} t={t} locale="en" />);
    expect(html).toContain('aria-label="Strength 5/5"');
    expect(html).toContain("positive");
  });

  it("never renders scenario-range numbers or numeric probabilities", () => {
    for (const signal of signals) {
      // Check visible text only: style attributes (e.g. margin-top:0.6rem)
      // are not user-facing and would otherwise false-positive.
      const text = visibleText(render(<SignalCard signal={signal} t={t} locale="en" />));
      for (const forbidden of internalNumbers()) {
        expect(text, `${signal.signalId} leaked ${forbidden}`).not.toContain(forbidden);
      }
      expect(text).not.toContain("scenario");
    }
  });

  it("renders probability as a qualitative band only", () => {
    const text = visibleText(render(<SignalCard signal={loudoun} t={t} locale="en" />));
    expect(text).toContain("probability tilt: moderate positive tilt");
    expect(text).not.toContain("0.66");
  });
});

describe("Provenance block", () => {
  it("lists every source with its retrieval date and the invalidation text", () => {
    const html = render(
      <Provenance sources={loudoun.sources} invalidation={loudoun.invalidation} t={t} locale="en" />,
    );
    expect(html).toContain("Invalidation condition");
    expect(html).toContain("Sources");
    expect(html).not.toContain("失效条件");
    expect((html.match(/<a /g) ?? []).length).toBe(loudoun.sources.length);
    expect(html).toContain("retrieved 2026-11-08");
  });
});

describe("merged signals fixture integrity", () => {
  it("has 14 rows with zero duplicate ids, all parsing through toPublicSignal", () => {
    expect(signalsFixture).toHaveLength(14);
    const ids = (signalsFixture as Array<{ signal_id: string }>).map((r) => r.signal_id);
    expect(new Set(ids).size).toBe(14);
    expect(signals).toHaveLength(14); // every row parses — mapper throws otherwise
  });

  it("kept all ten original curated signals and merged the four 2026-11-14 scan signals", () => {
    const ids = (signalsFixture as Array<{ signal_id: string }>).map((r) => r.signal_id);
    for (const id of [
      "SIG-2026-11-000118", "SIG-2026-11-000121", "SIG-2026-11-000117", "SIG-2026-11-000115",
      "SIG-2026-11-000112", "SIG-2026-11-000109", "SIG-2026-11-000106", "SIG-2026-11-000103",
      "SIG-2026-11-000101", "SIG-2026-10-000096",
    ]) {
      expect(ids).toContain(id);
    }
    for (const id of [
      "SIG-2026-11-000122", "SIG-2026-11-000123", "SIG-2026-11-000124", "SIG-2026-11-000125",
    ]) {
      expect(ids).toContain(id);
    }
  });

  it("maps every row to a known heat-card theme", () => {
    for (const signal of signals) {
      if (signal.theme) {
        expect(THEMES.some((t) => t.id === signal.theme), signal.theme).toBe(true);
      }
    }
  });
});
