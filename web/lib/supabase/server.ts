/**
 * Supabase Auth server client. Session cookies flow through next/headers in
 * server components and server actions; middleware.ts builds its own client
 * from the request/response pair. URL and anon key come from the same env
 * names the data layer already reads (NEXT_PUBLIC_SUPABASE_URL /
 * NEXT_PUBLIC_SUPABASE_ANON_KEY).
 *
 * No explicit SupabaseClient return annotations here: they pin the generic
 * parameters and break contextual typing of the cookie callbacks.
 */
import { createServerClient, type CookieMethodsServer } from "@supabase/ssr";

function supabaseAuthEnv(): { url: string; anonKey: string } {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL ?? process.env.SUPABASE_URL;
  const anonKey =
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? process.env.SUPABASE_ANON_KEY;
  if (!url || !anonKey) {
    throw new Error(
      "NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY must be set for Supabase Auth (or run with ALPHASTOCK_DATA_SOURCE=fixture)",
    );
  }
  return { url: url.replace(/\/$/, ""), anonKey };
}

/** Server-component / server-action client: session cookies via next/headers. */
export async function createSupabaseServerClient() {
  const { cookies } = await import("next/headers");
  const cookieStore = await cookies();
  const env = supabaseAuthEnv();
  const cookieMethods: CookieMethodsServer = {
    getAll: () => cookieStore.getAll(),
    setAll: (cookiesToSet) => {
      cookiesToSet.forEach(({ name, value, options }) => {
        try {
          cookieStore.set(name, value, options);
        } catch {
          // Called from a server component render (cookies are read-only
          // there); the middleware refreshes cookies on the response instead.
        }
      });
    },
  };
  return createServerClient(env.url, env.anonKey, { cookies: cookieMethods });
}

/**
 * Middleware client: session cookies read from the request and refreshed on
 * the response, per the @supabase/ssr middleware pattern. Lives here (not in
 * middleware.ts) so the cookie adapter is testable next to the server client.
 */
export function createSupabaseMiddlewareClient(
  url: string,
  anonKey: string,
  request: { cookies: { getAll: () => { name: string; value: string }[] } },
  setCookie: (name: string, value: string, options?: object) => void,
) {
  const cookieMethods: CookieMethodsServer = {
    getAll: () => request.cookies.getAll(),
    setAll: (cookiesToSet) => {
      cookiesToSet.forEach(({ name, value, options }) => setCookie(name, value, options));
    },
  };
  return createServerClient(url, anonKey, { cookies: cookieMethods });
}
