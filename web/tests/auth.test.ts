/**
 * Auth middleware tests. The verdicts are pure (lib/auth.ts decideAuth), so
 * the redirect and fixture-bypass branches are asserted directly; the
 * middleware adapter itself is exercised with next/server to prove fixture
 * mode makes ZERO network calls, and that the Supabase branch resolves the
 * session through the middleware client.
 */
process.env.ALPHASTOCK_DATA_SOURCE = "fixture";

import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LOGIN_PATH, decideAuth, nextFromQuery } from "../lib/auth";
import { middleware } from "../middleware";

/** Middleware-client stub — the Supabase branch below rewrites its verdicts. */
const { getUser } = vi.hoisted(() => ({ getUser: vi.fn() }));
vi.mock("@supabase/ssr", () => ({
  createServerClient: () => ({ auth: { getUser } }),
}));

function request(path: string): NextRequest {
  return new NextRequest(`http://localhost:3100${path}`);
}

/** Capture every attempted network call — fixture mode must keep this empty. */
let networkCalls: string[];
beforeEach(() => {
  networkCalls = [];
  vi.stubGlobal("fetch", (input: RequestInfo | URL) => {
    networkCalls.push(String(input));
    return Promise.reject(new Error("network disabled in test"));
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("decideAuth — pure verdicts", () => {
  it("fixture mode allows everything as the fake user, login page standalone", () => {
    const feed = decideAuth("/feed", { fixture: true, email: null });
    expect(feed).toEqual({ kind: "allow", shell: true, email: "researcher@gridcapital.net" });

    const login = decideAuth(LOGIN_PATH, { fixture: true, email: null });
    expect(login).toEqual({ kind: "allow", shell: false, email: "researcher@gridcapital.net" });
  });

  it("redirects unauthenticated requests to protected routes with a next param", () => {
    const decision = decideAuth("/themes", { fixture: false, email: null, query: "?year=2026" });
    expect(decision).toEqual({
      kind: "redirect",
      location: "/login?next=%2Fthemes%3Fyear%3D2026",
    });
  });

  it("keeps public paths public without a session", () => {
    for (const path of ["/login", "/lang", "/_next/static/chunk.js", "/weekly/report.md"]) {
      expect(decideAuth(path, { fixture: false, email: null })).toMatchObject({ kind: "allow" });
    }
  });

  it("bounces authenticated users away from /login and lets them through elsewhere", () => {
    expect(decideAuth(LOGIN_PATH, { fixture: false, email: "a@b.co" })).toEqual({
      kind: "redirect",
      location: "/feed",
    });
    expect(decideAuth("/feed", { fixture: false, email: "a@b.co" })).toEqual({
      kind: "allow",
      shell: true,
      email: "a@b.co",
    });
  });

  it("never treats a next param as an open redirect", () => {
    expect(nextFromQuery("next=https://evil.example")).toBe("/feed");
    expect(nextFromQuery("next=//evil.example")).toBe("/feed");
    expect(nextFromQuery("next=%2Fthemes")).toBe("/themes");
    expect(nextFromQuery(null)).toBe("/feed");
  });
});

describe("middleware adapter", () => {
  it("fixture mode: passes through with ZERO Supabase network calls", async () => {
    const response = await middleware(request("/feed"));
    expect(networkCalls).toEqual([]);
    expect(response.headers.get("x-middleware-next")).toBe("1");
  });

  it("fixture mode: login page renders standalone", async () => {
    const response = await middleware(request("/login"));
    expect(networkCalls).toEqual([]);
    expect(response.headers.get("x-middleware-next")).toBe("1");
  });
});

describe("middleware adapter — Supabase branch", () => {
  it("redirects unauthenticated users to /login?next=… and resolves the session via the middleware client", async () => {
    process.env.ALPHASTOCK_DATA_SOURCE = "supabase";
    process.env.NEXT_PUBLIC_SUPABASE_URL = "https://example.supabase.co";
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = "test-anon-key";
    getUser.mockResolvedValue({ data: { user: null }, error: null });

    const response = await middleware(request("/feed"));
    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe(
      "http://localhost:3100/login?next=%2Ffeed",
    );
    expect(getUser).toHaveBeenCalled();

    // Authenticated: the session email rides to the layout, request proceeds.
    getUser.mockResolvedValue({ data: { user: { email: "richard@gridcapital.net" } }, error: null });
    const allowed = await middleware(request("/feed"));
    expect(allowed.headers.get("x-middleware-next")).toBe("1");

    // Restore fixture mode for the remaining suites.
    process.env.ALPHASTOCK_DATA_SOURCE = "fixture";
  });

  it("fails closed to /login when Supabase is unreachable instead of 500-ing", async () => {
    process.env.ALPHASTOCK_DATA_SOURCE = "supabase";
    process.env.NEXT_PUBLIC_SUPABASE_URL = "https://example.supabase.co";
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = "test-anon-key";
    getUser.mockRejectedValue(new TypeError("fetch failed: ENOTFOUND"));

    const response = await middleware(request("/feed"));
    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe(
      "http://localhost:3100/login?next=%2Ffeed",
    );

    // Restore fixture mode for the remaining suites.
    process.env.ALPHASTOCK_DATA_SOURCE = "fixture";
  });
});
