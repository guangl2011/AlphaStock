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
import { toPublicSignal } from "../lib/view";

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
  it("renders the EN summary, ZH summary, and verification state", () => {
    const html = render(<SignalCard signal={loudoun} />);
    expect(html).toContain("Loudoun County");
    expect(html).toContain("劳登县");
    expect(html).toContain("verified ✓");
  });

  it("always shows the invalidation condition and source links", () => {
    const html = render(<SignalCard signal={loudoun} />);
    expect(html).toContain("Invalidation condition");
    expect(html).toContain("Board of Supervisors reversal");
    expect(html).toContain('href="https://propertyweb.loudoun.gov/dataminer/"');
  });

  it("exposes strength and direction as badges, not numbers pretending to be advice", () => {
    const html = render(<SignalCard signal={loudoun} />);
    expect(html).toContain('aria-label="strength 5 of 5"');
    expect(html).toContain("positive");
  });

  it("never renders scenario-range numbers or numeric probabilities", () => {
    for (const signal of signals) {
      // Check visible text only: style attributes (e.g. margin-top:0.6rem)
      // are not user-facing and would otherwise false-positive.
      const text = visibleText(render(<SignalCard signal={signal} />));
      for (const forbidden of internalNumbers()) {
        expect(text, `${signal.signalId} leaked ${forbidden}`).not.toContain(forbidden);
      }
      expect(text).not.toContain("scenario");
    }
  });

  it("renders probability as a qualitative band only", () => {
    const text = visibleText(render(<SignalCard signal={loudoun} />));
    expect(text).toContain("probability tilt: moderate positive tilt");
    expect(text).not.toContain("0.66");
  });
});

describe("Provenance block", () => {
  it("lists every source with its retrieval date and the invalidation text", () => {
    const html = render(
      <Provenance sources={loudoun.sources} invalidation={loudoun.invalidation} />,
    );
    expect(html).toContain("Invalidation condition · 失效条件");
    expect(html).toContain("Sources · 来源");
    expect((html.match(/<a /g) ?? []).length).toBe(loudoun.sources.length);
    expect(html).toContain("retrieved 2026-11-08");
  });
});
