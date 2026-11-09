/**
 * The seven coverage themes (design doc §3.1) and theme-heat computation.
 * `labelEn` values match the `themes` strings in pipelines/data/universe.csv
 * exactly (they are the join key from company rows to themes).
 */
import type { PublicSignal } from "./types";
import { recencyWeight } from "./score";

export interface ThemeConfig {
  /** Matches the universe CSV theme string. */
  id: string;
  labelEn: string;
  labelZh: string;
}

export const THEMES: ThemeConfig[] = [
  { id: "AI Power", labelEn: "AI Power", labelZh: "AI 电力发电" },
  { id: "Utilities", labelEn: "Utilities", labelZh: "公用事业" },
  { id: "Grid & Transmission", labelEn: "Grid & Transmission", labelZh: "电网与输电" },
  { id: "Cooling & Thermal", labelEn: "Cooling & Thermal", labelZh: "冷却与热管理" },
  { id: "Data Centers", labelEn: "Data Centers", labelZh: "数据中心" },
  { id: "Nuclear & Fuel Cells", labelEn: "Nuclear & Fuel Cells", labelZh: "核能与燃料电池" },
  { id: "Physical AI & Robotics", labelEn: "Physical AI & Robotics", labelZh: "Physical AI 与机器人" },
];

const THEME_IDS = new Set(THEMES.map((t) => t.id));

export type HeatLevel = "high" | "medium" | "low";

export interface ThemeHeat {
  theme: ThemeConfig;
  signalCount: number;
  /** Σ strength × recency decay across the 90-day window (same math as momentum). */
  weightedHeat: number;
  companyCount: number;
  level: HeatLevel;
}

/**
 * Theme heat from published signals + how many covered companies map to each
 * theme. All seven themes always appear, even with zero signals — an empty
 * theme is information, not a blank row.
 */
export function computeThemeHeat(
  signals: PublicSignal[],
  companyCounts: Record<string, number>,
): ThemeHeat[] {
  const byTheme = new Map<string, { count: number; heat: number }>();
  for (const signal of signals) {
    if (!signal.theme || !THEME_IDS.has(signal.theme)) continue;
    const entry = byTheme.get(signal.theme) ?? { count: 0, heat: 0 };
    entry.count += 1;
    entry.heat += signal.strength * recencyWeight(signal.publishedAt);
    byTheme.set(signal.theme, entry);
  }

  const maxHeat = Math.max(1, ...[...byTheme.values()].map((e) => e.heat));

  return THEMES.map((theme) => {
    const entry = byTheme.get(theme.id) ?? { count: 0, heat: 0 };
    const ratio = entry.heat / maxHeat;
    const level: HeatLevel = ratio >= 0.66 ? "high" : ratio >= 0.33 ? "medium" : "low";
    return {
      theme,
      signalCount: entry.count,
      weightedHeat: entry.heat,
      companyCount: companyCounts[theme.id] ?? 0,
      level,
    };
  }).sort((a, b) => b.weightedHeat - a.weightedHeat || b.signalCount - a.signalCount);
}

/** Count covered companies per theme from company rows' `themes` arrays. */
export function companyCountsByTheme(
  companies: { themes: string[] }[],
): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const theme of THEMES) counts[theme.id] = 0;
  for (const company of companies) {
    for (const theme of company.themes) {
      if (theme in counts) counts[theme] += 1;
    }
  }
  return counts;
}
