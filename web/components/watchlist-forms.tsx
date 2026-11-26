"use client";

import { useActionState } from "react";

import { addTickerAction, type AddTickerState } from "@/app/watchlist/actions";

const INITIAL: AddTickerState = { status: "idle" };

export interface AddTickerFormLabels {
  addLabel: string;
  placeholder: string;
  submit: string;
  submitting: string;
  invalid: string;
  duplicate: string;
  limit: string;
  service: string;
}

export interface AddTickerFormProps {
  labels: AddTickerFormLabels;
}

/**
 * Add-to-watchlist form. Labels arrive as flat strings computed on the
 * server — the dictionary's format functions never cross the RSC boundary.
 */
export function AddTickerForm({ labels }: AddTickerFormProps) {
  const [state, formAction, pending] = useActionState(addTickerAction, INITIAL);
  const showError =
    state.status === "invalid" ||
    state.status === "duplicate" ||
    state.status === "limit" ||
    state.status === "service";
  return (
    <form action={formAction} className="as-filters" aria-label={labels.addLabel}>
      <label>
        {labels.addLabel}
        <input
          type="text"
          name="ticker"
          placeholder={labels.placeholder}
          maxLength={6}
          required
          aria-invalid={(state.status === "invalid" || state.status === "duplicate") || undefined}
        />
      </label>
      <button type="submit" disabled={pending}>
        {pending ? labels.submitting : labels.submit}
      </button>
      {showError ? (
        <p className="as-login-error" role="alert">
          {state.status === "invalid"
            ? labels.invalid
            : state.status === "duplicate"
              ? labels.duplicate
              : state.status === "limit"
                ? labels.limit
                : labels.service}
        </p>
      ) : null}
    </form>
  );
}
