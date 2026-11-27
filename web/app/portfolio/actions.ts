"use server";

/**
 * Portfolio server actions. Results are discriminated unions — the client
 * form renders the localized message, never a raw error, and every failure
 * path returns. CSV imports are all-or-nothing: the parse runs before any
 * write, so a malformed row can never leave a partial import behind.
 */
import { revalidatePath } from "next/cache";

import {
  parseHoldingInput,
  parsePortfolioCsv,
  type CsvRowError,
  type HoldingField,
} from "@/lib/portfolio";
import {
  removeHolding,
  resolvePortfolioUserId,
  upsertHoldings,
} from "@/lib/portfolio-store";

export type AddHoldingState =
  | { status: "idle" }
  | { status: "field"; field: HoldingField }
  | { status: "ok" }
  | { status: "service" };

export async function addHoldingAction(
  _prev: AddHoldingState,
  formData: FormData,
): Promise<AddHoldingState> {
  const result = parseHoldingInput({
    ticker: String(formData.get("ticker") ?? ""),
    shares: String(formData.get("shares") ?? ""),
    costBasis: String(formData.get("cost_basis") ?? ""),
  });
  if (!result.ok) return { status: "field", field: result.field };

  try {
    const userId = await resolvePortfolioUserId();
    await upsertHoldings(userId, [result.holding]);
  } catch (error) {
    // Surfaced to the user as a retryable service state; logged for operators.
    console.error("portfolio add failed:", error);
    return { status: "service" };
  }
  revalidatePath("/portfolio");
  return { status: "ok" };
}

export type ImportCsvState =
  | { status: "idle" }
  | { status: "rejected"; errors: CsvRowError[] }
  | { status: "ok" }
  | { status: "service" };

export async function importCsvAction(
  _prev: ImportCsvState,
  formData: FormData,
): Promise<ImportCsvState> {
  const file = formData.get("csv");
  if (!(file instanceof File) || file.size === 0) {
    return { status: "rejected", errors: [{ line: 1, field: "header" }] };
  }

  const parsed = parsePortfolioCsv(await file.text());
  if (!parsed.ok) return { status: "rejected", errors: parsed.errors };

  try {
    const userId = await resolvePortfolioUserId();
    await upsertHoldings(userId, parsed.holdings);
  } catch (error) {
    console.error("portfolio import failed:", error);
    return { status: "service" };
  }
  revalidatePath("/portfolio");
  return { status: "ok" };
}

/** Plain form action (no state): the remove buttons in the holdings table. */
export async function removeHoldingAction(formData: FormData): Promise<void> {
  const ticker = String(formData.get("ticker") ?? "");
  if (!ticker) return;
  const userId = await resolvePortfolioUserId();
  await removeHolding(userId, ticker);
  revalidatePath("/portfolio");
}
