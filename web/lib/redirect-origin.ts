import type { NextRequest } from "next/server";

/**
 * Redirect target for the language switch. Behind the hosted proxy request.url
 * carries the internal origin (http://localhost:3000), so the browser was
 * bounced off the public site; the public origin arrives in the standard
 * x-forwarded-host / x-forwarded-proto headers. Without them (local dev,
 * direct traffic) the request origin itself is used.
 */
export function redirectTarget(request: NextRequest, next: string): URL {
  const host = firstForwardedValue(request.headers.get("x-forwarded-host"));
  const proto = firstForwardedValue(request.headers.get("x-forwarded-proto"));
  const origin = host ? `${proto === "http" ? "http" : "https"}://${host}` : request.url;
  return new URL(next, origin);
}

/** x-forwarded-* fields can be comma lists (multi-hop proxies); the first hop is the client-facing one. */
function firstForwardedValue(value: string | null): string | null {
  return value?.split(",")[0]?.trim() || null;
}
