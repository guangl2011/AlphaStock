/**
 * /lang route handler. Behind the hosted proxy request.url presents the
 * internal http://localhost:3000 origin, so the switch redirect must take the
 * public origin from the x-forwarded-host / x-forwarded-proto headers; the
 * same-site-only guard on `next` must survive unchanged.
 */
process.env.ALPHASTOCK_DATA_SOURCE = "fixture";

import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";

import { GET } from "../app/lang/route";
import { LOCALE_COOKIE } from "../lib/i18n";

function langRequest(next: string, headers: Record<string, string> = {}): NextRequest {
  return new NextRequest(`http://localhost:3000/lang?l=zh&next=${encodeURIComponent(next)}`, {
    headers,
  });
}

function redirectLocation(next: string, headers: Record<string, string> = {}): string | null {
  return GET(langRequest(next, headers)).headers.get("location");
}

describe("GET /lang redirect origin", () => {
  it("uses the forwarded host and proto behind the public proxy, keeping path and query", () => {
    expect(
      redirectLocation("/themes?year=2026&quarter=Q3", {
        "x-forwarded-host": "alphastock.example.com",
        "x-forwarded-proto": "https",
      }),
    ).toBe("https://alphastock.example.com/themes?year=2026&quarter=Q3");
  });

  it("takes the first host of a multi-hop forwarded list and defaults to https", () => {
    expect(
      redirectLocation("/feed", { "x-forwarded-host": "alpha.example.com, internal.local" }),
    ).toBe("https://alpha.example.com/feed");
  });

  it("ignores a non-http(s) forwarded proto", () => {
    expect(
      redirectLocation("/feed", {
        "x-forwarded-host": "alpha.example.com",
        "x-forwarded-proto": "javascript",
      }),
    ).toBe("https://alpha.example.com/feed");
  });

  it("falls back to the request origin without forwarded headers", () => {
    expect(redirectLocation("/themes")).toBe("http://localhost:3000/themes");
  });

  it("still refuses cross-site next paths even with forwarded headers", () => {
    expect(redirectLocation("//evil.example", { "x-forwarded-host": "alpha.example.com" })).toBe(
      "https://alpha.example.com/",
    );
  });

  it("sets the locale cookie on the redirect", () => {
    const response = GET(langRequest("/themes"));
    expect(response.cookies.get(LOCALE_COOKIE)?.value).toBe("zh");
  });
});
