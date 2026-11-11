/**
 * Hand-rolled SVG sparkline — no chart dependency. Points come from the
 * pure `sparklinePoints` helper so the math is testable without a renderer.
 */
export function Sparkline({ points, label }: { points: string; label: string }) {
  // A one-vertex polyline draws nothing — mark single-point series with a dot.
  const vertices = points === "" ? [] : points.split(" ");
  return (
    <svg
      viewBox="0 0 240 48"
      width="240"
      height="48"
      role="img"
      aria-label={label}
      style={{ display: "block", maxWidth: "100%" }}
    >
      <line
        x1="4"
        y1="44"
        x2="236"
        y2="44"
        stroke="var(--color-border)"
        strokeWidth="1"
      />
      {vertices.length === 1 ? (
        <circle
          cx={vertices[0].split(",")[0]}
          cy={vertices[0].split(",")[1]}
          r="4"
          fill="var(--brand-cyan)"
        />
      ) : (
        <polyline
          points={points}
          fill="none"
          stroke="var(--brand-cyan)"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      )}
    </svg>
  );
}
