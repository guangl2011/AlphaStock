/**
 * Row → view-model mappers. This file is the compliance wall:
 * `toPublicSignal` is the ONLY function allowed to turn a `signals` row into
 * something a component can render, and it drops `scenario_range` and the
 * numeric `probability_edge`, replacing them with a qualitative band. Free-tier
 * display rules (design doc §6) are thus unrepresentable in the view types.
 *
 * Rows come from PostgREST or fixture JSON — never trusted. Every mapper
 * validates shape and throws RowParseError rather than rendering garbage.
 */
import type {
  Direction,
  EventType,
  ProbabilityBand,
  PublicAlert,
  PublicAttestation,
  PublicCompany,
  PublicSignal,
  Sensitivity,
  SignalStrength,
  SourceLink,
} from "./types";

export class RowParseError extends Error {
  constructor(
    message: string,
    readonly row: unknown,
  ) {
    super(`row failed validation: ${message}`);
    this.name = "RowParseError";
  }
}

function asRecord(row: unknown): Record<string, unknown> {
  if (typeof row !== "object" || row === null || Array.isArray(row)) {
    throw new RowParseError("not an object", row);
  }
  return row as Record<string, unknown>;
}

function str(record: Record<string, unknown>, key: string): string {
  const value = record[key];
  if (typeof value !== "string" || value.length === 0) {
    throw new RowParseError(`${key} must be a non-empty string`, record);
  }
  return value;
}

function strOrNull(record: Record<string, unknown>, key: string): string | null {
  const value = record[key];
  if (value === null || value === undefined) return null;
  if (typeof value !== "string") throw new RowParseError(`${key} must be string|null`, record);
  return value;
}

function numOrNull(record: Record<string, unknown>, key: string): number | null {
  const value = record[key];
  if (value === null || value === undefined) return null;
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new RowParseError(`${key} must be number|null`, record);
  }
  return value;
}

function strArray(record: Record<string, unknown>, key: string): string[] {
  const value = record[key];
  if (value === null || value === undefined) return [];
  if (!Array.isArray(value) || value.some((v) => typeof v !== "string")) {
    throw new RowParseError(`${key} must be string[]`, record);
  }
  return value as string[];
}

const DIRECTIONS: Direction[] = ["positive", "negative", "neutral"];

function parseDirection(value: unknown, record: Record<string, unknown>): Direction {
  if (typeof value === "string" && DIRECTIONS.includes(value as Direction)) {
    return value as Direction;
  }
  throw new RowParseError(`invalid direction ${String(value)}`, record);
}

const EVENT_TYPES: EventType[] = [
  "new_project",
  "status_transition",
  "capacity_change",
  "withdrawal",
  "other",
];

function parseEventType(value: unknown, record: Record<string, unknown>): EventType {
  if (typeof value === "string" && EVENT_TYPES.includes(value as EventType)) {
    return value as EventType;
  }
  throw new RowParseError(`invalid event_type ${String(value)}`, record);
}

function parseStrength(value: unknown, record: Record<string, unknown>): SignalStrength {
  if (typeof value === "number" && Number.isInteger(value) && value >= 1 && value <= 5) {
    return value as SignalStrength;
  }
  throw new RowParseError(`strength must be an integer 1–5, got ${String(value)}`, record);
}

const SENSITIVITIES: Sensitivity[] = ["high", "medium", "low"];

function parseMacroOverlay(value: unknown, record: Record<string, unknown>): PublicSignal["macroOverlay"] {
  if (value === null || value === undefined) {
    return { rateSensitivity: null, usdSensitivity: null };
  }
  if (typeof value !== "object" || Array.isArray(value)) {
    throw new RowParseError("macro_overlay must be an object", record);
  }
  const macro = value as Record<string, unknown>;
  const sensitivity = (key: string): Sensitivity | null => {
    const v = macro[key];
    if (v === null || v === undefined) return null;
    if (typeof v === "string" && SENSITIVITIES.includes(v as Sensitivity)) return v as Sensitivity;
    throw new RowParseError(`macro_overlay.${key} invalid`, record);
  };
  return { rateSensitivity: sensitivity("rate_sensitivity"), usdSensitivity: sensitivity("usd_sensitivity") };
}

function parseSources(value: unknown, record: Record<string, unknown>): SourceLink[] {
  if (value === null || value === undefined) return [];
  if (!Array.isArray(value)) throw new RowParseError("sources must be an array", record);
  return value.map((entry, index) => {
    if (typeof entry !== "object" || entry === null) {
      throw new RowParseError(`sources[${index}] not an object`, record);
    }
    const source = entry as Record<string, unknown>;
    const url = source.url;
    if (typeof url !== "string" || !/^https?:\/\//.test(url)) {
      throw new RowParseError(`sources[${index}].url must be an http(s) URL`, record);
    }
    return {
      label:
        typeof source.type === "string" && source.type.length > 0
          ? source.type
          : "source",
      url,
      retrievedAt:
        typeof source.retrieved_at === "string" ? source.retrieved_at : null,
    };
  });
}

/**
 * Qualitative probability band from the numeric edge (0–1 likelihood scale,
 * 0.5 = balanced). The number itself is dropped here — paid-tier-only data
 * (design doc §6) must not leak through any render path.
 */
export function toProbabilityBand(edge: number | null): ProbabilityBand {
  if (edge === null || !Number.isFinite(edge)) return "balanced";
  const tilt = edge - 0.5;
  const magnitude = Math.abs(tilt);
  if (magnitude < 0.05) return "balanced";
  const grade = magnitude < 0.15 ? "slight" : magnitude < 0.25 ? "moderate" : "strong";
  return `${grade} ${tilt > 0 ? "positive" : "negative"}` as ProbabilityBand;
}

export function toPublicSignal(row: unknown): PublicSignal {
  const record = asRecord(row);
  return {
    signalId: str(record, "signal_id"),
    hash: str(record, "hash"),
    category: str(record, "category"),
    eventType: parseEventType(record.event_type, record),
    tickers: strArray(record, "tickers"),
    theme: strOrNull(record, "theme"),
    iso: strOrNull(record, "iso"),
    projectId: strOrNull(record, "project_id"),
    statusFrom: strOrNull(record, "status_from"),
    statusTo: strOrNull(record, "status_to"),
    summaryEn: str(record, "summary_en"),
    summaryZh: str(record, "summary_zh"),
    direction: parseDirection(record.direction, record),
    strength: parseStrength(record.strength, record),
    horizonDays: numOrNull(record, "horizon_days"),
    macroOverlay: parseMacroOverlay(record.macro_overlay, record),
    invalidation: str(record, "invalidation"),
    sources: parseSources(record.sources, record),
    verified: record.verified === true,
    publishedAt: str(record, "published_at"),
    probabilityBand: toProbabilityBand(
      numOrNull(record, "probability_edge"),
    ),
  };
}

export function toPublicCompany(row: unknown): PublicCompany {
  const record = asRecord(row);
  return {
    ticker: str(record, "ticker"),
    name: str(record, "name"),
    category: str(record, "category"),
    themes: strArray(record, "themes"),
    exposureType: strOrNull(record, "exposure_type"),
    marketCap: numOrNull(record, "market_cap"),
    marketCapAsOf: strOrNull(record, "market_cap_as_of"),
    thesis: strOrNull(record, "thesis"),
    keyRisk: strOrNull(record, "key_risk"),
    sourceUrl: str(record, "source_url"),
  };
}

export function toPublicAttestation(row: unknown): PublicAttestation {
  const record = asRecord(row);
  const batchHash = str(record, "batch_hash");
  if (!/^[0-9a-f]{64}$/.test(batchHash)) {
    throw new RowParseError("batch_hash must be 64 hex chars", record);
  }
  return {
    batchId: str(record, "batch_id"),
    batchHash,
    signalCount: numOrNull(record, "signal_count") ?? 0,
    signalIds: strArray(record, "signal_ids"),
    anchoredAt: str(record, "anchored_at"),
  };
}

export function toPublicAlert(row: unknown): PublicAlert {
  const record = asRecord(row);
  const embed = record.companies;
  return {
    alertId: str(record, "alert_id"),
    alertDate: str(record, "alert_date"),
    ticker: str(record, "ticker"),
    companyName:
      typeof embed === "object" && embed !== null && !Array.isArray(embed)
        ? strOrNull(embed as Record<string, unknown>, "name")
        : null,
    alphaScore: numOrNull(record, "alpha_score"),
    scoreDelta: numOrNull(record, "score_delta"),
    briefEn: str(record, "brief_en"),
    briefZh: str(record, "brief_zh"),
    signalIds: strArray(record, "signal_ids"),
    approved: record.approved === true,
    sentAt: strOrNull(record, "sent_at"),
  };
}
