"use client";

import { useActionState } from "react";

import {
  addHoldingAction,
  importCsvAction,
  type AddHoldingState,
  type ImportCsvState,
} from "@/app/portfolio/actions";
import type { Messages } from "@/lib/messages";

/** The client-safe label subset — no functions, so it crosses the RSC boundary. */
export type PortfolioFormLabels = Messages["portfolio"]["form"];

const ADD_INITIAL: AddHoldingState = { status: "idle" };
const IMPORT_INITIAL: ImportCsvState = { status: "idle" };

/**
 * Manual entry + CSV import. Validation errors are inline and field-level;
 * a rejected CSV lists every offending line — the whole-file rejection is
 * the product contract, so the copy says so explicitly.
 */
export function PortfolioForms({ labels }: { labels: PortfolioFormLabels }) {
  return (
    <div className="as-portfolio-forms">
      <AddHoldingForm labels={labels} />
      <ImportCsvForm labels={labels} />
    </div>
  );
}

function AddHoldingForm({ labels }: { labels: PortfolioFormLabels }) {
  const [state, formAction, pending] = useActionState(addHoldingAction, ADD_INITIAL);
  return (
    <form action={formAction} className="as-card as-portfolio-form" aria-label={labels.addTitle}>
      <h2>{labels.addTitle}</h2>
      <div className="as-portfolio-fields">
        <label>
          {labels.tickerLabel}
          <input name="ticker" required aria-invalid={state.status === "field" && state.field === "ticker" || undefined} />
        </label>
        <label>
          {labels.sharesLabel}
          <input name="shares" type="number" min="0" step="any" required aria-invalid={state.status === "field" && state.field === "shares" || undefined} />
        </label>
        <label>
          {labels.costLabel}
          <input name="cost_basis" type="number" min="0" step="any" aria-invalid={state.status === "field" && state.field === "cost_basis" || undefined} />
        </label>
      </div>
      {state.status === "field" ? (
        <p className="as-form-error" role="alert">
          {labels.fieldError[state.field]}
        </p>
      ) : null}
      {state.status === "service" ? (
        <p className="as-form-error" role="alert">
          {labels.serviceError}
        </p>
      ) : null}
      {state.status === "ok" ? (
        <p className="as-form-ok" role="status">
          {labels.addedOk}
        </p>
      ) : null}
      <button type="submit" disabled={pending}>
        {pending ? labels.working : labels.addButton}
      </button>
    </form>
  );
}

function ImportCsvForm({ labels }: { labels: PortfolioFormLabels }) {
  const [state, formAction, pending] = useActionState(importCsvAction, IMPORT_INITIAL);
  return (
    <form action={formAction} className="as-card as-portfolio-form" aria-label={labels.importTitle}>
      <h2>{labels.importTitle}</h2>
      <p className="as-meta">{labels.csvFormat}</p>
      <label>
        <input type="file" name="csv" accept=".csv,text/csv" required />
      </label>
      {state.status === "rejected" ? (
        <div className="as-form-error" role="alert">
          <p>{labels.importRejected}</p>
          <ul>
            {state.errors.map((error, index) => (
              <li key={`${error.line}-${error.field}-${index}`}>
                {labels.csvLinePrefix}
                {error.line}
                {labels.csvLineSuffix}: {labels.csvError[error.field]}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {state.status === "service" ? (
        <p className="as-form-error" role="alert">
          {labels.serviceError}
        </p>
      ) : null}
      {state.status === "ok" ? (
        <p className="as-form-ok" role="status">
          {labels.importOk}
        </p>
      ) : null}
      <button type="submit" disabled={pending}>
        {pending ? labels.working : labels.importButton}
      </button>
    </form>
  );
}
