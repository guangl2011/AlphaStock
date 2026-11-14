/**
 * The nav language switch must carry the full current location — path AND
 * query string — in the `next` param, so period selections such as
 * /themes?year=2026&quarter=Q3 survive a language switch.
 */
process.env.ALPHASTOCK_DATA_SOURCE = "fixture";

import { renderToString } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  usePathname: () => "/themes",
  useSearchParams: () => new URLSearchParams("year=2026&quarter=Q3"),
}));

import { SiteNav } from "../components/nav";
import { MESSAGES } from "../lib/messages";

describe("SiteNav language switch href", () => {
  it("carries the current path and query string in the next param", () => {
    const html = renderToString(<SiteNav locale="en" labels={MESSAGES.en.nav} />);
    // SSR escapes attribute ampersands; the encoded next value itself is ampersand-free.
    expect(html).toContain(`href="/lang?l=zh&amp;next=${encodeURIComponent("/themes?year=2026&quarter=Q3")}"`);
  });

  it("renders exactly one language switch link", () => {
    const html = renderToString(<SiteNav locale="en" labels={MESSAGES.en.nav} />);
    expect(html.match(/as-lang-switch/g)?.length).toBe(1);
  });
});
