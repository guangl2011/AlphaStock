/**
 * Data types for the Explorer.
 *
 * Two layers, and the wall between them is a compliance requirement:
 *  - `*Row` types mirror the Postgres/PostgREST column names (snake_case).
 *  - `Public*` view models are what components may render. `toPublicSignal`
 *    (lib/view.ts) is the only path from row to view model, and it strips
 *    numeric scenario ranges and probabilities — free-tier display rules
 *    (design doc §6) are enforced by the type system, not by component
 *    discipline.
 */

export type Direction = "positive" | "negative" | "neutral";
export type Sensitivity = "high" | "medium" | "low";
export type SignalStrength = 1 | 2 | 3 | 4 | 5;

export type EventType =
  | "new_project"
  | "status_transition"
  | "capacity_change"
  | "withdrawal"
  | "other";

/** Raw `signals` row (db/migrations/0001_signal_store.sql). */
export interface SignalRow {
  signal_id: string;
  hash: string;
  category: string;
  event_type: EventType;
  tickers: string[];
  theme: string | null;
  iso: string | null;
  project_id: string | null;
  status_from: string | null;
  status_to: string | null;
  summary_en: string;
  summary_zh: string;
  direction: Direction;
  strength: number;
  horizon_days: number | null;
  /** Internal modeling field (bear/base/bull excess-return bounds). Never rendered. */
  scenario_range: unknown;
  probability_edge: number | null;
  macro_overlay: unknown;
  invalidation: string;
  sources: unknown;
  verified: boolean;
  published_at: string;
}

/** Raw `companies` row. */
export interface CompanyRow {
  ticker: string;
  name: string;
  category: string;
  themes: string[];
  exposure_type: string | null;
  market_cap: number | null;
  market_cap_as_of: string | null;
  thesis: string | null;
  key_risk: string | null;
  source_url: string;
}

/** Raw `attestations` row. */
export interface AttestationRow {
  batch_id: string;
  batch_hash: string;
  signal_count: number;
  signal_ids: string[];
  anchored_at: string;
}

/** Raw `alert_history` row with the companies embed used by /alerts. */
export interface AlertRow {
  alert_id: string;
  alert_date: string;
  ticker: string;
  alpha_score: number | null;
  score_delta: number | null;
  brief_en: string;
  brief_zh: string;
  signal_ids: string[];
  approved: boolean;
  sent_at: string | null;
  companies?: { name: string; category: string } | null;
}

export interface SourceLink {
  label: string;
  url: string;
  retrievedAt: string | null;
}

/** Qualitative probability tilt — the numeric `probability_edge` never leaves the mapper. */
export type ProbabilityBand =
  | "balanced"
  | "slight positive"
  | "moderate positive"
  | "strong positive"
  | "slight negative"
  | "moderate negative"
  | "strong negative";

/** What a component may render for one signal. No scenario numbers exist here. */
export interface PublicSignal {
  signalId: string;
  hash: string;
  category: string;
  eventType: EventType;
  tickers: string[];
  theme: string | null;
  iso: string | null;
  projectId: string | null;
  statusFrom: string | null;
  statusTo: string | null;
  summaryEn: string;
  summaryZh: string;
  direction: Direction;
  strength: SignalStrength;
  horizonDays: number | null;
  macroOverlay: { rateSensitivity: Sensitivity | null; usdSensitivity: Sensitivity | null };
  invalidation: string;
  sources: SourceLink[];
  verified: boolean;
  publishedAt: string;
  probabilityBand: ProbabilityBand;
}

export interface PublicCompany {
  ticker: string;
  name: string;
  category: string;
  themes: string[];
  exposureType: string | null;
  marketCap: number | null;
  marketCapAsOf: string | null;
  thesis: string | null;
  keyRisk: string | null;
  sourceUrl: string;
}

export interface PublicAttestation {
  batchId: string;
  batchHash: string;
  signalCount: number;
  signalIds: string[];
  anchoredAt: string;
}

export interface PublicAlert {
  alertId: string;
  alertDate: string;
  ticker: string;
  companyName: string | null;
  alphaScore: number | null;
  scoreDelta: number | null;
  briefEn: string;
  briefZh: string;
  signalIds: string[];
  approved: boolean;
  sentAt: string | null;
}
