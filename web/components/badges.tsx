import type { Messages } from "@/lib/messages";
import type { Direction, SignalStrength } from "@/lib/types";

/** Direction badge — positive/negative/neutral, always rendered. */
export function DirectionBadge({ direction, t }: { direction: Direction; t: Messages }) {
  return <span className={`as-badge as-badge-${direction}`}>{t.badges[direction]}</span>;
}

/** Strength 1–5 as filled/empty dots — visual weight without fake precision. */
export function StrengthBadge({ strength, t }: { strength: SignalStrength; t: Messages }) {
  return (
    <span
      className="as-badge as-badge-neutral"
      aria-label={t.badges.strength(strength)}
      title={t.badges.strength(strength)}
    >
      {"●".repeat(strength)}
      {"○".repeat(5 - strength)}
    </span>
  );
}

export function ThemeBadge({ theme }: { theme: string }) {
  return <span className="as-badge as-badge-theme">{theme}</span>;
}

export function CategoryBadge({ category }: { category: string }) {
  return <span className="as-badge as-badge-category">{category}</span>;
}
