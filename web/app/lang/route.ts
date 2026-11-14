import { NextResponse, type NextRequest } from "next/server";

import { LOCALE_COOKIE, isLocale, safeNextPath } from "@/lib/i18n";

/**
 * Language switch: /lang?l=zh&next=/indices sets the locale cookie and returns
 * to the same page. Only same-site relative paths are accepted as `next`.
 */
export function GET(request: NextRequest) {
  const l = request.nextUrl.searchParams.get("l");
  const next = safeNextPath(request.nextUrl.searchParams.get("next"));
  // Relative Location: the browser resolves it against the current origin, so
  // no proxy hop — each rewriting request.url or x-forwarded-host — can bounce
  // the user off the canonical domain.
  const response = new NextResponse(null, { status: 307, headers: { Location: next } });
  if (isLocale(l)) {
    response.cookies.set(LOCALE_COOKIE, l, {
      path: "/",
      maxAge: 60 * 60 * 24 * 365,
      sameSite: "lax",
    });
  }
  return response;
}
