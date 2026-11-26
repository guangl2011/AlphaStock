import { formatStressPct } from "@/lib/format";
import type { PortfolioStress } from "@/lib/portfolio";
import type { Messages } from "@/lib/messages";

/**
 * Portfolio-level ±50bp rate-stress card. Both shocks render: +50bp
 * (negative — rising rates compress valuations) and −50bp (positive). The
 * coverage line discloses how much of the portfolio the weighted mean
 * actually covers; unrated positions are never silently treated as immune.
 */
export function RateStressCard({
  stress,
  labels,
}: {
  stress: PortfolioStress;
  labels: Messages["portfolio"];
}) {
  // One-decimal everywhere — meter attributes included (display rules).
  const up = Number(stress.up50ImpactPct.toFixed(1));
  const down = Number(stress.down50ImpactPct.toFixed(1));
  return (
    <section className="as-card as-stress-card" aria-label={labels.stressTitle}>
      <h2>{labels.stressTitle}</h2>
      <div className="as-stress-bars">
        <div className="as-stress-bar" aria-label={labels.stressUp}>
          <span className="as-stress-label">{labels.stressUp}</span>
          <meter min={-1.5} max={1.5} value={up} aria-hidden="true" />
          <span className="as-stress-value as-stress-negative">
            {formatStressPct(up)}
          </span>
        </div>
        <div className="as-stress-bar" aria-label={labels.stressDown}>
          <span className="as-stress-label">{labels.stressDown}</span>
          <meter min={-1.5} max={1.5} value={down} aria-hidden="true" />
          <span className="as-stress-value as-stress-positive">
            {formatStressPct(down)}
          </span>
        </div>
      </div>
      <p className="as-meta">
        {labels.stressCoverage(stress.ratedShares, stress.totalShares)}
      </p>
      <p className="as-meta">{labels.stressMethod}</p>
    </section>
  );
}
