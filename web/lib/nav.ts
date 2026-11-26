import type { Messages } from "./messages";

/**
 * Sidebar navigation contract. The eight Explorer routes migrate 1:1 from the
 * old top bar; /companies and /reports are new V2.1 pages and live in the same
 * rail. Order is the product contract — tests assert it.
 */
export interface NavItem {
  href: string;
  label: string;
  /** Fixed-width glyph shown in both rail states; text is hidden when collapsed. */
  icon: string;
}

const NAV_KEYS = [
  ["/feed", "feed", "📡"],
  ["/themes", "themes", "🗂"],
  ["/indices", "indices", "📈"],
  ["/research", "research", "🔬"],
  ["/factors", "factors", "🧮"],
  ["/sentiment", "sentiment", "💬"],
  ["/track-record", "trackRecord", "🏁"],
  ["/alerts", "alerts", "🔔"],
  ["/companies", "companies", "🏢"],
  ["/reports", "reports", "📰"],
] as const satisfies readonly (readonly [string, keyof Messages["nav"], string])[];

export type NavKey = (typeof NAV_KEYS)[number][1];

export function navItems(labels: Messages["nav"]): NavItem[] {
  return NAV_KEYS.map(([href, key, icon]) => ({ href, label: labels[key], icon }));
}
