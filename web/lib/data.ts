/**
 * Data access for the Explorer.
 *
 * Two modes, chosen once per server process:
 *  - "supabase" (default in production): read-only PostgREST queries against
 *    Supabase with the anon key. RLS (db/migrations/0002_rls.sql) restricts
 *    every table to SELECT for anon — the key carries no write path.
 *  - "fixture": static fixture rows shaped exactly like PostgREST responses.
 *    Selected explicitly via ALPHASTOCK_DATA_SOURCE=fixture for local dev,
 *    dogfooding, and CI — tests and e2e never touch live Supabase.
 *
 * If supabase mode is selected but env is missing, reads throw ConfigError →
 * the error boundary explains it. Falling back to fake data silently would be
 * dishonest; never rendering is the correct failure.
 */
import alertsFixture from "@/fixtures/alert-history.json";
import attestationsFixture from "@/fixtures/attestations.json";
import companiesFixture from "@/fixtures/companies.json";
import signalsFixture from "@/fixtures/signals.json";
import { applySignalFilters, signalQueryParams } from "./filters";
import type { SignalFilters } from "./filters";
import { companyCountsByTheme } from "./themes";
import type {
  PublicAlert,
  PublicAttestation,
  PublicCompany,
  PublicSignal,
} from "./types";
import {
  toPublicAlert,
  toPublicAttestation,
  toPublicCompany,
  toPublicSignal,
} from "./view";

export type DataSource = "supabase" | "fixture";

export class ConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConfigError";
  }
}

function supabaseEnv(): { url: string; key: string } | null {
  const url = process.env.SUPABASE_URL ?? process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key =
    process.env.SUPABASE_ANON_KEY ?? process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !key) return null;
  return { url: url.replace(/\/$/, ""), key };
}

export function activeDataSource(): DataSource {
  if (process.env.ALPHASTOCK_DATA_SOURCE === "fixture") return "fixture";
  if (process.env.ALPHASTOCK_DATA_SOURCE === "supabase") return "supabase";
  return supabaseEnv() ? "supabase" : "fixture";
}

/** True when the current render is demo data — pages surface this honestly. */
export function isFixtureMode(): boolean {
  return activeDataSource() === "fixture";
}

/**
 * Read-only PostgREST select. The anon key grants exactly what RLS allows
 * (SELECT); there is no write path through this module by construction.
 */
async function restSelect(table: string, params: URLSearchParams): Promise<unknown[]> {
  const env = supabaseEnv();
  if (!env) {
    throw new ConfigError(
      "SUPABASE_URL and SUPABASE_ANON_KEY must be set for supabase data mode",
    );
  }
  const response = await fetch(`${env.url}/rest/v1/${table}?${params}`, {
    headers: {
      apikey: env.key,
      Authorization: `Bearer ${env.key}`,
      Accept: "application/json",
    },
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`Supabase ${table} query failed: HTTP ${response.status}`);
  }
  const rows: unknown = await response.json();
  if (!Array.isArray(rows)) {
    throw new Error(`Supabase ${table} query returned a non-array payload`);
  }
  return rows;
}

/** Parse rows through the compliance mappers; a bad row fails the page loudly. */
function parseRows<Public>(
  rows: unknown[],
  mapper: (row: unknown) => Public,
  table: string,
): Public[] {
  return rows.map((row, index) => {
    try {
      return mapper(row);
    } catch (error) {
      throw new Error(`${table}[${index}] is malformed: ${(error as Error).message}`, {
        cause: error,
      });
    }
  });
}

export interface SignalsPage {
  signals: PublicSignal[];
  /** Present only in fixture mode — the UI shows a demo-data notice. */
  fixture: boolean;
}

export async function getSignals(filters: SignalFilters): Promise<SignalsPage> {
  if (isFixtureMode()) {
    const all = parseRows<PublicSignal>(
      signalsFixture as unknown[],
      toPublicSignal,
      "fixture:signals",
    );
    return { signals: applySignalFilters(all, filters), fixture: true };
  }
  const rows = await restSelect("signals", signalQueryParams(filters));
  return {
    signals: parseRows<PublicSignal>(rows, toPublicSignal, "signals"),
    fixture: false,
  };
}

export interface ThemesPage {
  signals: PublicSignal[];
  companyCounts: Record<string, number>;
  fixture: boolean;
}

export async function getThemesData(): Promise<ThemesPage> {
  if (isFixtureMode()) {
    const signals = parseRows<PublicSignal>(
      signalsFixture as unknown[],
      toPublicSignal,
      "fixture:signals",
    );
    const companies = parseRows<PublicCompany>(
      companiesFixture as unknown[],
      toPublicCompany,
      "fixture:companies",
    );
    return { signals, companyCounts: companyCountsByTheme(companies), fixture: true };
  }
  // Small tables — both are a few hundred rows at MVP scale.
  const [signalRows, companyRows] = await Promise.all([
    restSelect("signals", new URLSearchParams({ select: "*", order: "published_at.desc", limit: "200" })),
    restSelect("companies", new URLSearchParams({ select: "*", order: "ticker.asc" })),
  ]);
  const companies = parseRows<PublicCompany>(companyRows, toPublicCompany, "companies");
  return {
    signals: parseRows<PublicSignal>(signalRows, toPublicSignal, "signals"),
    companyCounts: companyCountsByTheme(companies),
    fixture: false,
  };
}

export interface CompanyPage {
  company: PublicCompany;
  signals: PublicSignal[];
  latestAlert: PublicAlert | null;
  fixture: boolean;
}

/** Full company page bundle; null when the ticker is not in coverage. */
export async function getCompanyPage(ticker: string): Promise<CompanyPage | null> {
  const normalizedTicker = ticker.toUpperCase();

  if (isFixtureMode()) {
    const companies = parseRows<PublicCompany>(
      companiesFixture as unknown[],
      toPublicCompany,
      "fixture:companies",
    );
    const company = companies.find((c) => c.ticker === normalizedTicker);
    if (!company) return null;
    const signals = parseRows<PublicSignal>(
      signalsFixture as unknown[],
      toPublicSignal,
      "fixture:signals",
    ).filter((signal) => signal.tickers.includes(normalizedTicker));
    const alerts = parseRows<PublicAlert>(
      alertsFixture as unknown[],
      toPublicAlert,
      "fixture:alert_history",
    )
      .filter((alert) => alert.ticker === normalizedTicker && alert.approved)
      .sort((a, b) => (a.alertDate < b.alertDate ? 1 : -1));
    return { company, signals, latestAlert: alerts[0] ?? null, fixture: true };
  }

  const companyRows = await restSelect(
    "companies",
    new URLSearchParams({ select: "*", ticker: `eq.${normalizedTicker}` }),
  );
  if (companyRows.length === 0) return null;
  const company = toPublicCompany(companyRows[0]);

  const [signalRows, alertRows] = await Promise.all([
    restSelect(
      "signals",
      new URLSearchParams({
        select: "*",
        tickers: `cs.{${normalizedTicker}}`,
        order: "published_at.desc",
      }),
    ),
    restSelect(
      "alert_history",
      new URLSearchParams({
        select: "*",
        ticker: `eq.${normalizedTicker}`,
        approved: "eq.true",
        order: "alert_date.desc",
        limit: "1",
      }),
    ),
  ]);

  return {
    company,
    signals: parseRows<PublicSignal>(signalRows, toPublicSignal, "signals"),
    latestAlert:
      parseRows<PublicAlert>(alertRows, toPublicAlert, "alert_history")[0] ??
      null,
    fixture: false,
  };
}

export interface TrackRecordPage {
  attestations: PublicAttestation[];
  signals: PublicSignal[];
  fixture: boolean;
}

export async function getTrackRecord(): Promise<TrackRecordPage> {
  if (isFixtureMode()) {
    return {
      attestations: parseRows<PublicAttestation>(
        attestationsFixture as unknown[],
        toPublicAttestation,
        "fixture:attestations",
      ).sort((a, b) => (a.anchoredAt < b.anchoredAt ? 1 : -1)),
      signals: parseRows<PublicSignal>(
        signalsFixture as unknown[],
        toPublicSignal,
        "fixture:signals",
      ),
      fixture: true,
    };
  }
  const [attestationRows, signalRows] = await Promise.all([
    restSelect("attestations", new URLSearchParams({ select: "*", order: "anchored_at.desc" })),
    restSelect("signals", new URLSearchParams({ select: "*", order: "published_at.desc" })),
  ]);
  return {
    attestations: parseRows<PublicAttestation>(
      attestationRows,
      toPublicAttestation,
      "attestations",
    ),
    signals: parseRows<PublicSignal>(signalRows, toPublicSignal, "signals"),
    fixture: false,
  };
}

export interface AlertsPage {
  alerts: PublicAlert[];
  fixture: boolean;
}

export async function getAlerts(): Promise<AlertsPage> {
  if (isFixtureMode()) {
    return {
      alerts: parseRows<PublicAlert>(
        alertsFixture as unknown[],
        toPublicAlert,
        "fixture:alert_history",
      ).sort((a, b) => (a.alertDate < b.alertDate ? 1 : -1)),
      fixture: true,
    };
  }
  const rows = await restSelect(
    "alert_history",
    new URLSearchParams({
      select: "*,companies(name,category)",
      order: "alert_date.desc",
    }),
  );
  return {
    alerts: parseRows<PublicAlert>(rows, toPublicAlert, "alert_history"),
    fixture: false,
  };
}
