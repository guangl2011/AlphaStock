/**
 * Signal momentum — the scoring engine's (PR #4) momentum dimension is the
 * spec's "Σ strength × recency-decay over 90 days". The Explorer recomputes
 * it for display (provisional score + theme heat) from published signals only:
 * deterministic math, no LLM, no runtime scoring dependency.
 */
import type { PublicSignal } from "./types";

export const MOMENTUM_WINDOW_DAYS = 90;

const DAY_MS = 86_400_000;

/**
 * Linear decay from 1.0 (today) to 0.0 at the 90-day window edge.
 * Signals dated slightly ahead of `now` (clock skew) count fully.
 */
export function recencyWeight(publishedAt: string, now: Date = new Date()): number {
  const publishedMs = Date.parse(publishedAt);
  if (Number.isNaN(publishedMs)) return 0;
  const ageDays = (now.getTime() - publishedMs) / DAY_MS;
  if (ageDays <= 0) return 1;
  return Math.max(0, 1 - ageDays / MOMENTUM_WINDOW_DAYS);
}

export function signalMomentum(signals: PublicSignal[], now: Date = new Date()): number {
  return signals.reduce(
    (sum, signal) => sum + signal.strength * recencyWeight(signal.publishedAt, now),
    0,
  );
}
