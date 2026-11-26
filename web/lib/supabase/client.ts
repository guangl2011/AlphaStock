/**
 * Supabase Auth browser client (client components); lazily-created singleton
 * per page load. User-scoped features that land client-side (PR-3 watchlist
 * star toggles) consume this instead of re-creating clients ad hoc.
 */
import { createBrowserClient } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";

let browserClient: SupabaseClient | undefined;
export function createSupabaseBrowserClient(): SupabaseClient {
  browserClient ??= createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL ?? "",
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "",
  );
  return browserClient;
}
