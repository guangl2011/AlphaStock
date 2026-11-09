import Link from "next/link";

import { CategoryBadge, DirectionBadge, StrengthBadge, ThemeBadge } from "./badges";
import { Provenance } from "./provenance";
import { categoryConfig } from "@/lib/categories";
import { formatDate, probabilityLabel } from "@/lib/format";
import type { PublicSignal } from "@/lib/types";

/**
 * The canonical signal render. Its input is a PublicSignal — the view type
 * that structurally cannot carry scenario ranges or numeric probabilities —
 * so no render path can violate the free-tier display rules.
 */
export function SignalCard({ signal }: { signal: PublicSignal }) {
  const category = categoryConfig(signal.category);
  const categoryLabel = category ? category.labelEn : signal.category;
  const categoryLabelZh = category ? category.labelZh : null;

  const eventMeta: string[] = [];
  if (signal.iso) eventMeta.push(signal.iso);
  if (signal.projectId) eventMeta.push(signal.projectId);
  if (signal.eventType === "status_transition" && signal.statusFrom && signal.statusTo) {
    eventMeta.push(`${signal.statusFrom} → ${signal.statusTo}`);
  } else if (signal.eventType === "status_transition" && signal.statusTo) {
    eventMeta.push(`→ ${signal.statusTo}`);
  }
  if (signal.horizonDays !== null) eventMeta.push(`${signal.horizonDays}d horizon`);

  return (
    <article className="as-card" aria-label={`signal ${signal.signalId}`}>
      <div
        style={{
          alignItems: "center",
          display: "flex",
          flexWrap: "wrap",
          gap: "0.4rem",
        }}
      >
        <DirectionBadge direction={signal.direction} />
        <StrengthBadge strength={signal.strength} />
        {signal.theme ? <ThemeBadge theme={signal.theme} /> : null}
        <CategoryBadge category={categoryLabel} />
        {signal.verified ? (
          <span className="as-badge as-badge-positive">verified ✓</span>
        ) : (
          <span className="as-badge as-badge-neutral">unverified</span>
        )}
      </div>

      <h3 className="as-card-title" style={{ marginTop: "0.6rem" }}>
        {signal.summaryEn}
      </h3>
      <p className="as-summary-zh" lang="zh-Hans">
        {signal.summaryZh}
      </p>

      {signal.tickers.length > 0 ? (
        <p className="as-meta" style={{ margin: "0.6rem 0 0" }}>
          Related:{" "}
          {signal.tickers.map((ticker, index) => (
            <span key={ticker}>
              {index > 0 && ", "}
              <Link href={`/companies/${ticker}`}>{ticker}</Link>
            </span>
          ))}
        </p>
      ) : null}

      <p className="as-meta" style={{ margin: "0.3rem 0 0" }}>
        {eventMeta.length > 0 ? `${eventMeta.join(" · ")} · ` : ""}
        published {formatDate(signal.publishedAt)} · probability tilt:{" "}
        {probabilityLabel(signal.probabilityBand, "en")}{" "}
        <span lang="zh-Hans">（{probabilityLabel(signal.probabilityBand, "zh")}）</span>
      </p>

      <Provenance sources={signal.sources} invalidation={signal.invalidation} />
      <p className="as-meta" style={{ margin: "0.5rem 0 0", fontSize: "0.7rem" }}>
        {signal.signalId} · hash {signal.hash.slice(0, 12)}… · {categoryLabelZh ?? ""}
      </p>
    </article>
  );
}
