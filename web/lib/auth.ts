/**
 * Auth plumbing for the V2.1 login shell.
 *
 * The decision logic here is pure so the middleware stays a thin adapter:
 * `decideAuth` maps a request path + resolved session to an allow/redirect
 * verdict, and the middleware supplies the session (a fixture fake user, or a
 * Supabase Auth session). The layout then reads the decision through request
 * headers rather than re-resolving the session — one Supabase round trip per
 * request, not two.
 */
import { safeNextPath } from "./i18n";

export const LOGIN_PATH = "/login";
/** Session marker the middleware forwards to server components. */
export const AUTH_EMAIL_HEADER = "x-as-auth-email";
/** "1" = render the sidebar shell; "0" = standalone page (login). */
export const AUTH_SHELL_HEADER = "x-as-auth-shell";
/** Cookie that marks the fixture-mode demo session (never sent to Supabase). */
export const FIXTURE_SESSION_COOKIE = "as_fixture_session";
/** Demo identity used in fixture mode — the shell tests assert on it. */
export const FIXTURE_USER_EMAIL = "researcher@gridcapital.net";

export type AuthDecision =
  | { kind: "allow"; shell: boolean; email: string }
  | { kind: "redirect"; location: string };

export function supabaseAuthEnv(): { url: string; anonKey: string } | null {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !anonKey) return null;
  return { url: url.replace(/\/$/, ""), anonKey };
}

/**
 * Fixture bypass (CI-critical): the same mode switch the data layer uses.
 * Fixture mode must be reachable with zero Supabase configuration and zero
 * network — mirroring `activeDataSource()` keeps auth and data in step.
 */
export function authFixtureMode(): boolean {
  if (process.env.ALPHASTOCK_DATA_SOURCE === "fixture") return true;
  if (process.env.ALPHASTOCK_DATA_SOURCE === "supabase") return false;
  return supabaseAuthEnv() === null;
}

/** Paths reachable without a session: the login page, the language jump, static assets. */
export function isPublicPath(pathname: string): boolean {
  if (pathname === LOGIN_PATH) return true;
  if (pathname === "/lang") return true;
  if (pathname.startsWith("/_next") || pathname === "/favicon.ico") return true;
  // Static files (e.g. /weekly/report.md) — anything with a file extension.
  return /\.[\w]+$/.test(pathname);
}

export interface DecideAuthInput {
  fixture: boolean;
  /** Resolved session email, or null when unauthenticated. */
  email: string | null;
  /** Raw query string (with leading "?") of the incoming request, if any. */
  query?: string;
}

/**
 * The whole guard in one pure function:
 *  - fixture mode: a fake user everywhere except the standalone login page;
 *  - public paths pass through without a session;
 *  - everything else redirects unauthenticated requests to /login?next=…
 *    and bounces already-authenticated users away from /login.
 */
export function decideAuth(pathname: string, input: DecideAuthInput): AuthDecision {
  if (input.fixture) {
    if (pathname === LOGIN_PATH) return { kind: "allow", shell: false, email: FIXTURE_USER_EMAIL };
    return { kind: "allow", shell: true, email: FIXTURE_USER_EMAIL };
  }
  // Authenticated users never see the login form again — /login is a page,
  // not a public path, for them.
  if (pathname === LOGIN_PATH) {
    return input.email
      ? { kind: "redirect", location: "/feed" }
      : { kind: "allow", shell: false, email: "" };
  }
  if (isPublicPath(pathname)) {
    return { kind: "allow", shell: false, email: input.email ?? "" };
  }
  if (!input.email) {
    const target = input.query ? `${pathname}${input.query}` : pathname;
    return { kind: "redirect", location: `${LOGIN_PATH}?next=${encodeURIComponent(target)}` };
  }
  return { kind: "allow", shell: true, email: input.email };
}

/** ?next= from a login redirect, constrained to same-site relative paths; unsafe or missing values land on /feed. */
export function nextFromQuery(query: string | null | undefined): string {
  if (!query) return "/feed";
  const params = new URLSearchParams(query.startsWith("?") ? query.slice(1) : query);
  const safe = safeNextPath(params.get("next"));
  return safe === "/" ? "/feed" : safe;
}
