/**
 * Feed filter parsing + application. One `SignalFilters` shape drives both the
 * in-memory fixture path and the PostgREST query-string path, so /feed behaves
 * identically in both data modes.
 */
import type { PublicSignal } from "./types";

export interface SignalFilters {
  theme?: string;
  category?: string;
  minStrength?: number;
}

export type SearchParamsInput = Record<string, string | string[] | undefined>;

function firstValue(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

/** Tolerant parse: unknown or malformed values are treated as unset. */
export function parseSignalFilters(params: SearchParamsInput): SignalFilters {
  const theme = firstValue(params.theme);
  const category = firstValue(params.category);
  const strengthRaw = firstValue(params.strength);
  const strength = Number.parseInt(strengthRaw ?? "", 10);

  return {
    theme: theme || undefined,
    category: category || undefined,
    minStrength: strength >= 1 && strength <= 5 ? strength : undefined,
  };
}

export function hasActiveFilters(filters: SignalFilters): boolean {
  return (
    filters.theme !== undefined ||
    filters.category !== undefined ||
    filters.minStrength !== undefined
  );
}

export function applySignalFilters(
  signals: PublicSignal[],
  filters: SignalFilters,
): PublicSignal[] {
  return signals.filter(
    (signal) =>
      (filters.theme === undefined || signal.theme === filters.theme) &&
      (filters.category === undefined || signal.category === filters.category) &&
      (filters.minStrength === undefined || signal.strength >= filters.minStrength),
  );
}

/** PostgREST query params for the Supabase path, newest first. */
export function signalQueryParams(filters: SignalFilters, limit = 200): URLSearchParams {
  const params = new URLSearchParams();
  params.set("select", "*");
  if (filters.theme) params.set("theme", `eq.${filters.theme}`);
  if (filters.category) params.set("category", `eq.${filters.category}`);
  if (filters.minStrength !== undefined) {
    params.set("strength", `gte.${filters.minStrength}`);
  }
  params.set("order", "published_at.desc");
  params.set("limit", String(limit));
  return params;
}
