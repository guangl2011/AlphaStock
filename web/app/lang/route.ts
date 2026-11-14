import { NextResponse, type NextRequest } from "next/server";

import { LOCALE_COOKIE, isLocale, safeNextPath } from "@/lib/i18n";
import { redirectTarget } from "@/lib/redirect-origin";

/**
 * Language switch: /lang?l=zh&next=/indices sets the locale cookie and returns
 * to the same page. Only same-site relative paths are accepted as `next`.
 */
export function GET(request: NextRequest) {
  const l = request.nextUrl.searchParams.get("l");
  const next = safeNextPath(request.nextUrl.searchParams.get("next"));
  const response = NextResponse.redirect(redirectTarget(request, next));
  if (isLocale(l)) {
    response.cookies.set(LOCALE_COOKIE, l, {
      path: "/",
      maxAge: 60 * 60 * 24 * 365,
      sameSite: "lax",
    });
  }
  return response;
}

