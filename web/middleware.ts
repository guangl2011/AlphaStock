/**
 * Route guard: every page lives behind the login wall except the login page,
 * the language jump, and static assets. Fixture mode bypasses the guard
 * entirely — a fake user passes through with zero Supabase network calls,
 * so CI and local demos stay deterministic and offline.
 *
 * The allow/redirect verdicts themselves are pure (lib/auth.ts); this file is
 * the adapter that resolves the session and forwards the verdict to the
 * server-rendered layout via request headers.
 */
import { NextResponse, type NextRequest } from "next/server";

import {
  AUTH_EMAIL_HEADER,
  AUTH_SHELL_HEADER,
  authFixtureMode,
  decideAuth,
  supabaseAuthEnv,
} from "@/lib/auth";
import { createSupabaseMiddlewareClient } from "@/lib/supabase/server";

export async function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl;

  if (authFixtureMode()) {
    return applyDecision(request, decideAuth(pathname, { fixture: true, email: null }));
  }

  const env = supabaseAuthEnv();
  if (!env) {
    // Supabase mode requested without configuration: fail closed — every
    // protected route lands on /login, where the form surfaces the service
    // error when the user attempts to sign in.
    return applyDecision(
      request,
      decideAuth(pathname, { fixture: false, email: null, query: search }),
    );
  }

  // Response object exists first so refreshed auth cookies ride on whatever
  // verdict comes back (allow or redirect).
  const response = NextResponse.next({ request: { headers: request.headers } });
  const supabase = createSupabaseMiddlewareClient(env.url, env.anonKey, request, (
    name, value, options,
  ) => response.cookies.set(name, value, options));
  let email: string | null = null;
  try {
    const { data } = await supabase.auth.getUser();
    email = data.user?.email ?? null;
  } catch {
    // Supabase unreachable (network down, project paused): fail closed —
    // treat as signed out rather than 500-ing; /login renders the service
    // error when the user attempts to sign in.
    email = null;
  }

  const decision = decideAuth(pathname, { fixture: false, email, query: search });
  if (decision.kind === "redirect") {
    // Refreshed auth cookies must ride the redirect too.
    return redirectWithCookies(decision.location, request, response);
  }
  return applyDecision(request, decision, response);
}

/** Refreshed session cookies ride every verdict, including redirects. */
function redirectWithCookies(
  location: string,
  request: NextRequest,
  refreshed?: NextResponse,
): NextResponse {
  const redirect = NextResponse.redirect(new URL(location, request.url));
  if (refreshed) {
    for (const cookie of refreshed.cookies.getAll()) {
      redirect.cookies.set(cookie);
    }
  }
  return redirect;
}

/** Forward the verdict to the layout: shell on/off + session email. */
function applyDecision(
  request: NextRequest,
  decision: ReturnType<typeof decideAuth>,
  response?: NextResponse,
) {
  if (decision.kind === "redirect") {
    return redirectWithCookies(decision.location, request, response);
  }
  const headers = new Headers(request.headers);
  headers.set(AUTH_SHELL_HEADER, decision.shell ? "1" : "0");
  if (decision.email) headers.set(AUTH_EMAIL_HEADER, decision.email);
  return NextResponse.next({ request: { headers } });
}

export const config = {
  // Skip _next internals and anything that looks like a static file; the
  // code-level isPublicPath() still double-checks the remainder.
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.[\\w]+$).*)"],
};
