/**
 * /lang route handler. The hosted proxy follows 3xx responses server-side and
 * drops their Set-Cookie, which killed the Chinese switch on the public
 * dashboard — so the route answers with a 200 HTML jump page: the cookie
 * travels in the Set-Cookie header and a document.cookie mirror, and the
 * browser navigates to the relative `next` path itself. The same-site-only
 * guard on `next` stays unchanged.
 */
process.env.ALPHASTOCK_DATA_SOURCE = "fixture";

import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";

import { GET } from "../app/lang/route";
import { LOCALE_COOKIE } from "../lib/i18n";

function langRequest(params: string, headers?: Record<string, string>): NextRequest {
  return new NextRequest(`http://localhost:3000/lang?${params}`, { headers });
}

async function htmlFor(params: string, headers?: Record<string, string>): Promise<string> {
  return GET(langRequest(params, headers)).text();
}

function langParams(next: string): string {
  return `l=zh&next=${encodeURIComponent(next)}`;
}

describe("GET /lang jump page", () => {
  it("returns 200 text/html with no-store instead of a redirect", () => {
    const response = GET(langRequest("l=zh&next=%2F"));
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toContain("text/html");
    expect(response.headers.get("cache-control")).toBe("no-store");
  });

  it("sets the locale cookie in the header and the inline script", async () => {
    const response = GET(langRequest("l=zh&next=%2Fresearch%3Fperiod%3D2022"));
    expect(response.cookies.get(LOCALE_COOKIE)?.value).toBe("zh");
    const html = await response.text();
    expect(html).toContain(
      'document.cookie = "as_lang=zh; Path=/; Max-Age=31536000; SameSite=Lax";',
    );
  });

  it("jumps to the deep link via location.replace and a matching meta refresh", async () => {
    const html = await htmlFor("l=zh&next=%2Fresearch%3Fperiod%3D2022");
    expect(html).toContain('location.replace("/research?period=2022");');
    expect(html).toContain('http-equiv="refresh"');
    expect(html).toContain('content="0; url=/research?period=2022"');
  });

  it("keeps the jump target relative even behind forwarded headers", async () => {
    const html = await htmlFor("l=zh&next=%2Fthemes%3Fyear%3D2026%26quarter%3DQ3", {
      "x-forwarded-host": "alphastock.example.com, internal.local",
      "x-forwarded-proto": "https",
    });
    expect(html).toContain('location.replace("/themes?year=2026&quarter=Q3");');
    expect(html.includes("https://")).toBe(false);
  });

  it.each(["javascript:alert(1)", "https://evil.example"])(
    "sanitizes the cross-site target %s to /",
    async (target) => {
      const html = await htmlFor(langParams(target));
      expect(html).toContain('location.replace("/");');
      expect(html).toContain('content="0; url=/"');
      expect(html).not.toContain(target);
    },
  );

  it("still refuses protocol-relative next paths", async () => {
    const html = await htmlFor(langParams("//evil.example"));
    expect(html).toContain('location.replace("/");');
  });

  it("jumps without setting any cookie for an unknown locale", async () => {
    const request = langRequest("l=xx&next=%2Fthemes");
    const response = GET(request);
    expect(response.headers.getSetCookie().length).toBe(0);
    expect(response.cookies.get(LOCALE_COOKIE)).toBeUndefined();
    const html = await response.text();
    expect(html).toContain('location.replace("/themes");');
    expect(html).not.toContain("document.cookie");
  });
});
