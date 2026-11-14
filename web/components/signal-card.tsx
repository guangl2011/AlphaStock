import Link from "next/link";

import { CategoryBadge, DirectionBadge, StrengthBadge, ThemeBadge } from "./badges";
import { Provenance } from "./provenance";
import { categoryConfig } from "@/lib/categories";
import { formatDate, probabilityLabel } from "@/lib/format";
import type { Locale } from "@/lib/i18n";
import type { Messages } from "@/lib/messages";
import { themeLabel } from "@/lib/themes";
import type { PublicSignal } from "@/lib/types";

/**
 * The canonical signal render. Its input is a PublicSignal — the view type
 * that structurally cannot carry scenario ranges or numeric probabilities —
 * so no render path can violate the free-tier display rules. Exactly one
 * language renders: the summary, labels and badges all follow the locale.
 */
export function SignalCard({
  signal,
  t,
  locale,
}: {
  signal: PublicSignal;
  t: Messages;
  locale: Locale;
}) {
  const category = categoryConfig(signal.category);
  const categoryLabel = category
    ? locale === "zh" ? category.labelZh : category.labelEn
    : signal.category;

  const eventMeta: string[] = [];
  if (signal.iso) eventMeta.push(signal.iso);
  if (signal.projectId) eventMeta.push(signal.projectId);
  if (signal.eventType === "status_transition" && signal.statusFrom && signal.statusTo) {
    eventMeta.push(`${signal.statusFrom} → ${signal.statusTo}`);
  } else if (signal.eventType === "status_transition" && signal.statusTo) {
    eventMeta.push(`→ ${signal.statusTo}`);
  }
  if (signal.horizonDays !== null) eventMeta.push(t.signal.horizon(signal.horizonDays));

  return (
    <article className="as-card" aria-label={`signal ${signal.signalId}`}>
      <div style={{ alignItems: "center", display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
        <DirectionBadge direction={signal.direction} t={t} />
        <StrengthBadge strength={signal.strength} t={t} />
        {signal.theme ? <ThemeBadge theme={themeLabel(signal.theme, locale)} /> : null}
        <CategoryBadge category={categoryLabel} />
        {signal.verified ? (
          <span className="as-badge as-badge-positive">{t.badges.verified}</span>
        ) : (
          <span className="as-badge as-badge-neutral">{t.badges.unverified}</span>
        )}
      </div>

      <h3 className="as-card-title" style={{ marginTop: "0.6rem" }}>
        {locale === "zh" ? signal.summaryZh : signal.summaryEn}
      </h3>

      {signal.tickers.length > 0 ? (
        <p className="as-meta" style={{ margin: "0.6rem 0 0" }}>
          {t.signal.related}{" "}
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
        {t.signal.published(formatDate(signal.publishedAt))} ·{" "}
        {t.signal.probability(probabilityLabel(signal.probabilityBand, locale))}
      </p>

      <Provenance sources={signal.sources} invalidation={signal.invalidation} t={t} locale={locale} />
      <p className="as-meta" style={{ margin: "0.5rem 0 0", fontSize: "0.7rem" }}>
        {signal.signalId} · {t.signal.hash(signal.hash.slice(0, 12))}
      </p>
    </article>
  );
}
