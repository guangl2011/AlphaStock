import { NextResponse, type NextRequest } from "next/server";

import { LOCALE_COOKIE, isLocale, safeNextPath } from "@/lib/i18n";

const COOKIE_MAX_AGE = 60 * 60 * 24 * 365;

/** Escape for safe embedding in HTML text and double-quoted attributes. */
function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

/**
 * JSON string literal safe inside a <script> element: JSON.stringify escapes
 * quotes and backslashes, and rewriting `<` rules out a `</script>` break-out.
 */
function scriptLiteral(value: string): string {
  return JSON.stringify(value).replaceAll("<", "\\u003c");
}

/**
 * Self-contained HTML jump page. The hosted proxy follows 3xx responses
 * server-side and drops their Set-Cookie, so the switch cannot be a redirect:
 * a 200 page sails through any proxy and the browser finishes the job —
 * cookie via Set-Cookie header here, mirrored by document.cookie for proxies
 * that strip response cookies, then navigation via JS with a meta refresh and
 * a plain link as fallbacks.
 */
function jumpPage(next: string, locale: string | null): string {
  const setCookie = locale
    ? `document.cookie = ${scriptLiteral(
        `${LOCALE_COOKIE}=${locale}; Path=/; Max-Age=${COOKIE_MAX_AGE}; SameSite=Lax`,
      )};`
    : "";
  return `<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="0; url=${escapeHtml(next)}">
</head>
<body>
<script>
${setCookie}
location.replace(${scriptLiteral(next)});
</script>
<p><a href="${escapeHtml(next)}">Continue</a></p>
</body>
</html>
`;
}

/**
 * Language switch: /lang?l=zh&next=/indices sets the locale cookie and returns
 * to the same page. Only same-site relative paths are accepted as `next`.
 */
export function GET(request: NextRequest) {
  const l = request.nextUrl.searchParams.get("l");
  const next = safeNextPath(request.nextUrl.searchParams.get("next"));
  const locale = isLocale(l) ? l : null;
  const response = new NextResponse(jumpPage(next, locale), {
    status: 200,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-store",
    },
  });
  if (locale) {
    response.cookies.set(LOCALE_COOKIE, locale, {
      path: "/",
      maxAge: COOKIE_MAX_AGE,
      sameSite: "lax",
    });
  }
  return response;
}
