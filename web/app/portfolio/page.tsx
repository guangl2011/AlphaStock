import { removeHoldingAction } from "./actions";
import { EmptyState } from "@/components/empty-state";
import { FixtureNotice } from "@/components/fixture-notice";
import { PortfolioForms } from "@/components/portfolio-forms";
import { RateStressCard } from "@/components/rate-stress-card";
import { formatDelta, formatQuantity, formatStressPct } from "@/lib/format";
import { getAlerts, getSignals, isFixtureMode } from "@/lib/data";
import { getI18n } from "@/lib/i18n";
import {
  buildPortfolioRows,
  portfolioStress,
} from "@/lib/portfolio";
import {
  getPortfolio,
  resolvePortfolioUserId,
} from "@/lib/portfolio-store";

export const dynamic = "force-dynamic";

export async function generateMetadata() {
  const { t } = await getI18n();
  return { title: t.meta.portfolio };
}

/**
 * Portfolio Monitor (design doc v2.1, §7.2). User-scoped by construction:
 * the holdings come from the session's portfolio store (Supabase
 * user_portfolios or the fixture in-memory store), joined with the same
 * published signals/alerts every other page reads. The stress columns are
 * deterministic — identical holdings always render identical numbers.
 */
export default async function PortfolioPage() {
  const { t, locale } = await getI18n();
  const userId = await resolvePortfolioUserId();
  const [holdings, signalsPage, alertsPage] = await Promise.all([
    getPortfolio(userId),
    getSignals({}),
    getAlerts(),
  ]);

  const rows = buildPortfolioRows(holdings, signalsPage.signals, alertsPage.alerts);
  const stress = portfolioStress(
    rows.map((row) => ({
      shares: row.holding.shares,
      rateSensitivity: row.rateSensitivity,
    })),
  );

  return (
    <>
      <h1>{t.portfolio.title}</h1>
      <p className="as-meta">{t.portfolio.intro}</p>

      {rows.length === 0 ? (
        <EmptyState title={t.portfolio.emptyTitle} hint={t.portfolio.emptyHint} />
      ) : (
        <>
          <RateStressCard stress={stress} labels={t.portfolio} />
          <div className="as-table-wrap">
            <table className="as-table as-table-compact as-portfolio-table">
              <thead>
                <tr>
                  <th scope="col">{t.portfolio.columns.ticker}</th>
                  <th scope="col">{t.portfolio.columns.shares}</th>
                  <th scope="col">{t.portfolio.columns.cost}</th>
                  <th scope="col">{t.portfolio.columns.alphaScore}</th>
                  <th scope="col">{t.portfolio.columns.signal}</th>
                  <th scope="col">{t.portfolio.columns.sensitivity}</th>
                  <th scope="col">{t.portfolio.columns.up50}</th>
                  <th scope="col">{t.portfolio.columns.down50}</th>
                  <th scope="col">{t.portfolio.columns.remove}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.holding.ticker}>
                    <th scope="row">{row.holding.ticker}</th>
                    <td>{formatQuantity(row.holding.shares)}</td>
                    <td>{formatQuantity(row.holding.costBasis)}</td>
                    <td>
                      {row.alphaScore === null
                        ? "—"
                        : `${row.alphaScore} (${formatDelta(row.scoreDelta)})`}
                    </td>
                    <td className="as-wrap-cell">
                      {row.signal === null
                        ? "—"
                        : locale === "zh"
                          ? row.signal.summaryZh
                          : row.signal.summaryEn}
                    </td>
                    <td>
                      {row.rateSensitivity === null
                        ? t.portfolio.sensitivity.unknown
                        : t.portfolio.sensitivity[row.rateSensitivity]}
                    </td>
                    <td className={row.up50ImpactPct === null ? undefined : "as-stress-negative"}>
                      {formatStressPct(row.up50ImpactPct)}
                    </td>
                    <td className={row.down50ImpactPct === null ? undefined : "as-stress-positive"}>
                      {formatStressPct(row.down50ImpactPct)}
                    </td>
                    <td>
                      <form action={removeHoldingAction}>
                        <input type="hidden" name="ticker" value={row.holding.ticker} />
                        <button
                          type="submit"
                          className="as-remove-button"
                          aria-label={t.portfolio.removeAria(row.holding.ticker)}
                        >
                          ×
                        </button>
                      </form>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <PortfolioForms labels={t.portfolio.form} />
      {isFixtureMode() ? <FixtureNotice t={t} /> : null}
    </>
  );
}
