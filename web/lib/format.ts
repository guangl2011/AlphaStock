/**
 * Formatting helpers — pure, bilingual-aware where relevant.
 */

/** "2026-11-08" from an ISO timestamp; null passthrough for nullable columns. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = Date.parse(iso);
  if (Number.isNaN(date)) return "—";
  return new Date(date).toISOString().slice(0, 10);
}

/** Compact USD market cap: 4.51e12 → "$4.51T", 7.365e10 → "$73.7B", 5e8 → "$500M". */
export function formatMarketCap(usd: number | null | undefined): string {
  if (usd === null || usd === undefined || !Number.isFinite(usd)) return "—";
  if (usd >= 1e12) return `$${trim(usd / 1e12)}T`;
  if (usd >= 1e9) return `$${trim(usd / 1e9)}B`;
  if (usd >= 1e6) return `$${trim(usd / 1e6)}M`;
  return `$${trim(usd)}`;
}

function trim(value: number): string {
  return Number(value.toFixed(2)).toString();
}

/** Signed delta "+0.7" / "−1.2" / "0.0" for score movements. */
export function formatDelta(delta: number | null | undefined): string {
  if (delta === null || delta === undefined || !Number.isFinite(delta)) return "—";
  if (delta > 0) return `+${delta.toFixed(1)}`;
  if (delta < 0) return `−${Math.abs(delta).toFixed(1)}`;
  return "0.0";
}

/** Probability band → bilingual qualitative label (free-tier display rule). */
export function probabilityLabel(band: string, lang: "en" | "zh"): string {
  const labels: Record<string, [string, string]> = {
    balanced: ["balanced", "均衡"],
    "slight positive": ["slight positive tilt", "偏正面，概率优势较小"],
    "moderate positive": ["moderate positive tilt", "偏正面，概率优势中等"],
    "strong positive": ["strong positive tilt", "显著偏正面"],
    "slight negative": ["slight negative tilt", "偏负面，概率劣势较小"],
    "moderate negative": ["moderate negative tilt", "偏负面，概率劣势中等"],
    "strong negative": ["strong negative tilt", "显著偏负面"],
  };
  const entry = labels[band];
  return entry ? (lang === "zh" ? entry[1] : entry[0]) : band;
}
