"use server";

/**
 * Auth server actions: sign-in (Supabase email/password or the fixture-mode
 * bypass), sign-out, and password change. Results are discriminated unions —
 * the login form renders the localized message, never a raw error, and never
 * spins forever: every failure path returns.
 */
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import {
  FIXTURE_SESSION_COOKIE,
  FIXTURE_USER_EMAIL,
  LOGIN_PATH,
  authFixtureMode,
  nextFromQuery,
} from "@/lib/auth";
import { createSupabaseServerClient } from "@/lib/supabase/server";

export type SignInState = { status: "idle" } | { status: "invalid" } | { status: "service" };

/** Invalid credentials are a 400/422 from Supabase Auth; anything else is a service failure. */
function credentialError(error: { status?: number }): SignInState {
  return error.status === 400 || error.status === 422
    ? { status: "invalid" }
    : { status: "service" };
}

async function startFixtureSession(): Promise<void> {
  const store = await cookies();
  store.set(FIXTURE_SESSION_COOKIE, FIXTURE_USER_EMAIL, {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
  });
}

export async function signInAction(
  _prev: SignInState,
  formData: FormData,
): Promise<SignInState> {
  const next = nextFromQuery(
    typeof formData.get("next") === "string" ? (formData.get("next") as string) : null,
  );

  if (authFixtureMode()) {
    await startFixtureSession();
    redirect(next);
  }

  const email = String(formData.get("email") ?? "").trim();
  const password = String(formData.get("password") ?? "");
  if (!email || !password) return { status: "invalid" };

  try {
    const supabase = await createSupabaseServerClient();
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) return credentialError(error);
  } catch {
    // Unreachable Supabase (network down, project paused) — a service error,
    // not infinite loading and not a credentials message.
    return { status: "service" };
  }
  redirect(next);
}

/** Fixture-mode entry from the login page — sets the demo session, no Supabase. */
export async function fixtureEnterAction(formData: FormData): Promise<void> {
  const next = nextFromQuery(
    typeof formData.get("next") === "string" ? (formData.get("next") as string) : null,
  );
  await startFixtureSession();
  redirect(next);
}

export async function signOutAction(): Promise<void> {
  if (!authFixtureMode()) {
    const supabase = await createSupabaseServerClient();
    await supabase.auth.signOut();
  }
  const store = await cookies();
  store.delete(FIXTURE_SESSION_COOKIE);
  redirect(LOGIN_PATH);
}

export type ChangePasswordState =
  | { status: "idle" }
  | { status: "ok" }
  | { status: "too-short" }
  | { status: "service" };

export async function changePasswordAction(
  _prev: ChangePasswordState,
  formData: FormData,
): Promise<ChangePasswordState> {
  const password = String(formData.get("password") ?? "");
  if (password.length < 8) return { status: "too-short" };
  try {
    const supabase = await createSupabaseServerClient();
    const { error } = await supabase.auth.updateUser({ password });
    if (error) return { status: "service" };
  } catch {
    return { status: "service" };
  }
  return { status: "ok" };
}
