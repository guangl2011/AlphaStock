import type { Direction, SignalStrength } from "@/lib/types";

/** Direction badge — positive/negative/neutral, always rendered. */
export function DirectionBadge({ direction }: { direction: Direction }) {
  const label = { positive: "▲ positive", negative: "▼ negative", neutral: "● neutral" }[
    direction
  ];
  return <span className={`as-badge as-badge-${direction}`}>{label}</span>;
}

/** Strength 1–5 as filled/empty dots — visual weight without fake precision. */
export function StrengthBadge({ strength }: { strength: SignalStrength }) {
  return (
    <span
      className="as-badge as-badge-neutral"
      aria-label={`strength ${strength} of 5`}
      title={`Strength ${strength}/5`}
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
