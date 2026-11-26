import Link from "next/link";

import { EmptyState } from "@/components/empty-state";
import { FixtureNotice } from "@/components/fixture-notice";
import { getAlerts } from "@/lib/data";
import { formatDate } from "@/lib/format";
import { getI18n } from "@/lib/i18n";
import { groupByWeek } from "@/lib/reports";

export const dynamic = "force-dynamic";

export async function generateMetadata() {
  const { t } = await getI18n();
  return { title: t.meta.reports };
}

/**
 * Weekly report index — one card per published week. The aggregate Monday
 * scan report is the static /weekly/report.md artifact; per-company briefs
 * link into the same alert history the /alerts page details.
 */
export default async function ReportsPage() {
  const { t } = await getI18n();
  const { alerts, fixture } = await getAlerts();
  const weeks = groupByWeek(alerts);

  return (
    <>
      <h1 className="as-page-title">{t.reports.title}</h1>
      <p className="as-page-sub">{t.reports.sub}</p>
      {fixture ? <FixtureNotice t={t} /> : null}

      {weeks.length === 0 ? (
        <EmptyState title={t.reports.emptyTitle} hint={t.reports.emptyHint} />
      ) : (
        <ul className="as-report-list">
          {weeks.map((week) => (
            <li key={week.alertDate} className="as-card">
              <div style={{ alignItems: "center", display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
                <h2 className="as-report-week">{t.reports.weekOf(formatDate(week.alertDate))}</h2>
                <span className="as-badge as-badge-neutral">{t.reports.briefs(week.alerts.length)}</span>
              </div>
              <p className="as-meta" style={{ margin: "0.4rem 0" }}>
                {week.alerts.map((alert, index) => (
                  <span key={alert.alertId}>
                    {index > 0 && " · "}
                    <Link href={`/companies/${alert.ticker}`}>{alert.ticker}</Link>
                    {alert.alphaScore !== null ? ` (${alert.alphaScore})` : ""}
                  </span>
                ))}
              </p>
              <a href="/weekly/report.md" target="_blank" rel="noopener">
                {t.reports.aggregate} →
              </a>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
