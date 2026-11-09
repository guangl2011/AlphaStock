import Link from "next/link";
import { notFound } from "next/navigation";

import { DirectionBadge } from "@/components/badges";
import { ThemeBadge } from "@/components/badges";
import { EmptyState } from "@/components/empty-state";
import { FixtureNotice } from "@/components/fixture-notice";
import { MacroOverlayLine } from "@/components/macro-overlay";
import { Provenance } from "@/components/provenance";
import { getCompanyPage } from "@/lib/data";
import { formatDate, formatDelta, formatMarketCap, probabilityLabel } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function CompanyPage({
  params,
}: {
  params: Promise<{ ticker: string }>;
}) {
  const { ticker: rawTicker } = await params;
  const page = await getCompanyPage(rawTicker);
  if (!page) notFound();

  const { company, signals, latestAlert, fixture } = page;
  const score = latestAlert
    ? {
        value: latestAlert.alphaScore,
        delta: latestAlert.scoreDelta,
        asOf: latestAlert.alertDate,
      }
    : null;

  // Current view = the strongest recent signal touching this company
  // (strength first, then recency). Qualitative framing only.
  const currentView = [...signals].sort(
    (a, b) => b.strength - a.strength || (a.publishedAt < b.publishedAt ? 1 : -1),
  )[0] ?? null;

  return (
    <>
      <p className="as-meta">
        <Link href="/feed">← Signal feed</Link>
      </p>
      <header className="as-company-header">
        <h1 className="as-page-title" style={{ margin: 0 }}>
          {company.ticker} <span className="as-company-name">· {company.name}</span>
        </h1>
        <p className="as-meta" style={{ margin: "0.4rem 0 0" }}>
          {company.category} · market cap {formatMarketCap(company.marketCap)}{" "}
          (as of {company.marketCapAsOf}) · source:{" "}
          <a href={company.sourceUrl} target="_blank" rel="noopener noreferrer">
            {new URL(company.sourceUrl).host}
          </a>
        </p>
        <div style={{ alignItems: "center", display: "flex", flexWrap: "wrap", gap: "0.4rem", marginTop: "0.6rem" }}>
          {company.themes.map((themeId) => (
            <ThemeBadge key={themeId} theme={themeId} />
          ))}
        </div>
      </header>
      {fixture ? <FixtureNotice /> : null}

      <div className="as-company-grid">
        <section className="as-card" aria-label="alpha score">
          <h2 className="as-section-title">Alpha Score</h2>
          {score ? (
            <>
              <p className="as-score" style={{ margin: "0.4rem 0" }}>
                {score.value}
                <span className="as-meta" style={{ marginLeft: "0.6rem" }}>
                  {formatDelta(score.delta)} vs last week
                </span>
              </p>
              <p className="as-meta" style={{ margin: 0 }}>
                Published weekly alert score, as of {score.asOf}. Composite of signal momentum
                and vertical-signal contribution — a research ranking, not buy/sell advice.{" "}
                <span lang="zh-Hans">每周警报发布的综合评分，仅供研究排序，不构成买卖建议。</span>
              </p>
            </>
          ) : (
            <>
              <p className="as-score as-score-provisional" style={{ margin: "0.4rem 0" }}>
                —
              </p>
              <p className="as-meta" style={{ margin: 0 }}>
                No weekly alert has been published for this name yet, so no score is shown.{" "}
                <span lang="zh-Hans">该股票暂无每周警报评分。</span>
              </p>
            </>
          )}
        </section>

        <section className="as-card" aria-label="coverage thesis">
          <h2 className="as-section-title">Coverage · 覆盖逻辑</h2>
          <p style={{ margin: "0.4rem 0" }}>{company.thesis}</p>
          <p className="as-meta" style={{ margin: 0 }}>
            Key risk: {company.keyRisk}
          </p>
        </section>
      </div>

      <section className="as-card" aria-label="current view" style={{ marginTop: "1rem" }}>
        <h2 className="as-section-title">Current view · 当前观点</h2>
        {currentView ? (
          <>
            <div style={{ alignItems: "center", display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
              <DirectionBadge direction={currentView.direction} />
              <span className="as-meta">
                probability tilt {probabilityLabel(currentView.probabilityBand, "en")}{" "}
                <span lang="zh-Hans">（{probabilityLabel(currentView.probabilityBand, "zh")}）</span>
                {" · "}{currentView.horizonDays ?? 90}-day research horizon
              </span>
            </div>
            <p className="as-card-title" style={{ margin: "0.5rem 0 0.3rem" }}>
              {currentView.summaryEn}
            </p>
            <p className="as-summary-zh" lang="zh-Hans">
              {currentView.summaryZh}
            </p>
            <MacroOverlayLine
              rateSensitivity={currentView.macroOverlay.rateSensitivity}
              usdSensitivity={currentView.macroOverlay.usdSensitivity}
            />
            <Provenance sources={currentView.sources} invalidation={currentView.invalidation} />
            <p className="as-meta" style={{ margin: 0 }}>
              Driven by {currentView.signalId}, published {formatDate(currentView.publishedAt)}.
            </p>
          </>
        ) : (
          <EmptyState
            title="No current view"
            hint="Signals will appear here once research touching this company publishes."
          />
        )}
      </section>

      <section aria-label="signal timeline" style={{ marginTop: "1.25rem" }}>
        <h2 className="as-section-title">Signal timeline · 信号时间线</h2>
        {signals.length === 0 ? (
          <EmptyState
            title="No signals yet"
            hint="This company has no published signals in the current window."
          />
        ) : (
          <ul className="as-timeline">
            {signals.map((signal) => (
              <li key={signal.signalId} className="as-timeline-item">
                <span className="as-timeline-date">{formatDate(signal.publishedAt)}</span>
                <div className="as-timeline-body">
                  <DirectionBadge direction={signal.direction} />
                  <p style={{ margin: "0.4rem 0 0" }}>{signal.summaryEn}</p>
                  <p className="as-summary-zh" lang="zh-Hans">
                    {signal.summaryZh}
                  </p>
                  <p className="as-meta" style={{ margin: "0.3rem 0 0" }}>
                    {signal.signalId} · strength {signal.strength}/5
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
