"use client";

import { useActionState } from "react";

import {
  fixtureEnterAction,
  signInAction,
  type SignInState,
} from "@/lib/auth-actions";
import type { Messages } from "@/lib/messages";

const INITIAL: SignInState = { status: "idle" };

export interface LoginFormProps {
  fixture: boolean;
  /** Post-login landing path, already constrained to same-site relative paths. */
  next: string;
  labels: Messages["login"];
}

/**
 * The login card. Fixture mode swaps the credential form for an honest
 * "enter" action that starts the demo session without touching Supabase.
 */
export function LoginForm({ fixture, next, labels }: LoginFormProps) {
  if (fixture) {
    return (
      <form action={fixtureEnterAction} className="as-login-card">
        <h1>{labels.title}</h1>
        <p className="as-login-note">{labels.fixtureNote}</p>
        <input type="hidden" name="next" value={next} />
        <button type="submit" className="as-login-submit">
          {labels.fixtureEnter}
        </button>
      </form>
    );
  }
  return <SupabaseLoginForm next={next} labels={labels} />;
}

function SupabaseLoginForm({ next, labels }: { next: string; labels: Messages["login"] }) {
  const [state, formAction, pending] = useActionState(signInAction, INITIAL);
  return (
    <form action={formAction} className="as-login-card">
      <h1>{labels.title}</h1>
      <label className="as-login-field">
        {labels.email}
        <input
          name="email"
          type="email"
          autoComplete="email"
          required
          aria-invalid={state.status === "invalid" || undefined}
        />
      </label>
      <label className="as-login-field">
        {labels.password}
        <input
          name="password"
          type="password"
          autoComplete="current-password"
          required
          aria-invalid={state.status === "invalid" || undefined}
        />
      </label>
      {state.status === "invalid" ? (
        <p className="as-login-error" role="alert">
          {labels.invalid}
        </p>
      ) : null}
      {state.status === "service" ? (
        <div className="as-login-service" role="alert">
          <p>{labels.service}</p>
          <p>{labels.retry}</p>
        </div>
      ) : null}
      <input type="hidden" name="next" value={next} />
      <button type="submit" className="as-login-submit" disabled={pending}>
        {pending ? labels.submitting : labels.submit}
      </button>
    </form>
  );
}
