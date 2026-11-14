/**
 * /lang route handler. The redirect Location must be exactly the relative
 * `next` path: browsers resolve it against the current origin, so no proxy
 * hop (rewriting request.url or x-forwarded-host) can send the user off the
 * canonical domain. The same-site-only guard on `next` stays unchanged.
 */
process.env.ALPHASTOCK_DATA_SOURCE = "fixture";

import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";

import { GET } from "../app/lang/route";
import { LOCALE_COOKIE } from "../lib/i18n";

function langRequest(next: string): NextRequest {
  return new NextRequest(`http://localhost:3000/lang?l=zh&next=${encodeURIComponent(next)}`);
}

function redirectLocation(next: string): string | null {
  return GET(langRequest(next)).headers.get("location");
}

describe("GET /lang redirect", () => {
  it("returns a relative Location carrying the path and query, with no origin", () => {
    expect(redirectLocation("/themes?year=2026&quarter=Q3")).toBe(
      "/themes?year=2026&quarter=Q3",
    );
  });

  it("never produces an absolute URL, even behind forwarded headers", () => {
    const request = new NextRequest(
      "http://localhost:3000/lang?l=zh&next=%2Fthemes%3Fyear%3D2026%26quarter%3DQ3",
      {
        headers: {
          "x-forwarded-host": "alphastock.example.com, internal.local",
          "x-forwarded-proto": "https",
        },
      },
    );
    const location = GET(request).headers.get("location");
    expect(location).toBe("/themes?year=2026&quarter=Q3");
    expect(location?.startsWith("http")).toBe(false);
  });

  it("still refuses cross-site next paths", () => {
    expect(redirectLocation("//evil.example")).toBe("/");
  });

  it("sets the locale cookie on the redirect", () => {
    const response = GET(langRequest("/themes"));
    expect(response.cookies.get(LOCALE_COOKIE)?.value).toBe("zh");
  });

  it("redirects without touching the cookie for an unknown locale", () => {
    const request = new NextRequest("http://localhost:3000/lang?l=xx&next=%2Fthemes");
    const response = GET(request);
    expect(response.headers.get("location")).toBe("/themes");
    expect(response.cookies.get(LOCALE_COOKIE)).toBeUndefined();
  });
});
