import Link from "next/link";
import { notFound } from "next/navigation";

import { DirectionBadge, ThemeBadge } from "@/components/badges";
import { EmptyState } from "@/components/empty-state";
import { EnglishOnly, FixtureNotice } from "@/components/fixture-notice";
import { MacroOverlayLine } from "@/components/macro-overlay";
import { Provenance } from "@/components/provenance";
import { getCompanyPage } from "@/lib/data";
import { formatDate, formatDelta, formatMarketCap, probabilityLabel } from "@/lib/format";
import { getI18n } from "@/lib/i18n";
import { themeLabel } from "@/lib/themes";

export const dynamic = "force-dynamic";

export default async function CompanyPage({
  params,
}: {
  params: Promise<{ ticker: string }>;
}) {
  const { locale, t } = await getI18n();
  const { ticker: rawTicker } = await params;
  const page = await getCompanyPage(rawTicker);
  if (!page) notFound();

  const { company, signals, latestAlert, fixture } = page;
  const score = latestAlert
    ? { value: latestAlert.alphaScore, delta: latestAlert.scoreDelta, asOf: latestAlert.alertDate }
    : null;
  const category = t.universeCategories[company.category] ?? company.category;

  // Current view = the strongest recent signal touching this company
  // (strength first, then recency). Qualitative framing only.
  const currentView =
    [...signals].sort(
      (a, b) => b.strength - a.strength || (a.publishedAt < b.publishedAt ? 1 : -1),
    )[0] ?? null;

  return (
    <>
      <p className="as-meta">
        <Link href="/feed">{t.company.back}</Link>
      </p>
      <header className="as-company-header">
        <h1 className="as-page-title" style={{ margin: 0 }}>
          {company.ticker} <span className="as-company-name">· {company.name}</span>
        </h1>
        <p className="as-meta" style={{ margin: "0.4rem 0 0" }}>
          {t.company.meta(category, formatMarketCap(company.marketCap), company.marketCapAsOf ?? "—")}{" "}
          <a href={company.sourceUrl} target="_blank" rel="noopener noreferrer">
            {new URL(company.sourceUrl).host}
          </a>
        </p>
        <div style={{ alignItems: "center", display: "flex", flexWrap: "wrap", gap: "0.4rem", marginTop: "0.6rem" }}>
          {company.themes.map((themeId) => (
            <ThemeBadge key={themeId} theme={themeLabel(themeId, locale)} />
          ))}
        </div>
      </header>
      {fixture ? <FixtureNotice t={t} /> : null}

      <div className="as-company-grid">
        <section className="as-card" aria-label="alpha score">
          <h2 className="as-section-title">{t.company.score}</h2>
          {score ? (
            <>
              <p className="as-score" style={{ margin: "0.4rem 0" }}>
                {score.value}
                <span className="as-meta" style={{ marginLeft: "0.6rem" }}>
                  {t.company.vsLastWeek(formatDelta(score.delta))}
                </span>
              </p>
              <p className="as-meta" style={{ margin: 0 }}>{t.company.scoreNote(score.asOf)}</p>
            </>
          ) : (
            <>
              <p className="as-score as-score-provisional" style={{ margin: "0.4rem 0" }}>—</p>
              <p className="as-meta" style={{ margin: 0 }}>{t.company.noScore}</p>
            </>
          )}
        </section>

        <section className="as-card" aria-label="coverage thesis">
          <h2 className="as-section-title">{t.company.coverage}</h2>
          <EnglishOnly t={t} show={locale === "zh"} />
          <p style={{ margin: "0.4rem 0" }} lang="en">{company.thesis}</p>
          <p className="as-meta" style={{ margin: 0 }}>
            {t.company.keyRisk} <span lang="en">{company.keyRisk}</span>
          </p>
        </section>
      </div>

      <section className="as-card" aria-label="current view" style={{ marginTop: "1rem" }}>
        <h2 className="as-section-title">{t.company.currentView}</h2>
        {currentView ? (
          <>
            <div style={{ alignItems: "center", display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
              <DirectionBadge direction={currentView.direction} t={t} />
              <span className="as-meta">
                {t.company.tilt(
                  probabilityLabel(currentView.probabilityBand, locale),
                  currentView.horizonDays ?? 90,
                )}
              </span>
            </div>
            <p className="as-card-title" style={{ margin: "0.5rem 0 0.3rem" }}>
              {locale === "zh" ? currentView.summaryZh : currentView.summaryEn}
            </p>
            <MacroOverlayLine
              rateSensitivity={currentView.macroOverlay.rateSensitivity}
              usdSensitivity={currentView.macroOverlay.usdSensitivity}
              t={t}
            />
            <Provenance sources={currentView.sources} invalidation={currentView.invalidation} t={t} locale={locale} />
            <p className="as-meta" style={{ margin: 0 }}>
              {t.company.drivenBy(currentView.signalId, formatDate(currentView.publishedAt))}
            </p>
          </>
        ) : (
          <EmptyState title={t.company.noViewTitle} hint={t.company.noViewHint} />
        )}
      </section>

      <section aria-label="signal timeline" style={{ marginTop: "1.25rem" }}>
        <h2 className="as-section-title">{t.company.timeline}</h2>
        {signals.length === 0 ? (
          <EmptyState title={t.company.noSignalsTitle} hint={t.company.noSignalsHint} />
        ) : (
          <ul className="as-timeline">
            {signals.map((signal) => (
              <li key={signal.signalId} className="as-timeline-item">
                <span className="as-timeline-date">{formatDate(signal.publishedAt)}</span>
                <div className="as-timeline-body">
                  <DirectionBadge direction={signal.direction} t={t} />
                  <p style={{ margin: "0.4rem 0 0" }}>
                    {locale === "zh" ? signal.summaryZh : signal.summaryEn}
                  </p>
                  <p className="as-meta" style={{ margin: "0.3rem 0 0" }}>
                    {signal.signalId} · {t.company.strength(signal.strength)}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
