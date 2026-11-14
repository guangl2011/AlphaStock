import Link from "next/link";

import { EmptyState } from "@/components/empty-state";
import { FixtureNotice } from "@/components/fixture-notice";
import { getAlerts } from "@/lib/data";
import { formatDate, formatDelta } from "@/lib/format";
import { getI18n } from "@/lib/i18n";

export const dynamic = "force-dynamic";

export async function generateMetadata() {
  const { t } = await getI18n();
  return { title: t.meta.alerts };
}

/**
 * Weekly alert history — the Monday top-10 briefs after verification and the
 * research owner's approval gate. Rows awaiting approval show as pending: they
 * were never sent, and the history says so rather than hiding them.
 */
export default async function AlertsPage() {
  const { locale, t } = await getI18n();
  const { alerts, fixture } = await getAlerts();

  return (
    <>
      <h1 className="as-page-title">{t.alerts.title}</h1>
      <p className="as-page-sub">{t.alerts.sub}</p>
      {fixture ? <FixtureNotice t={t} /> : null}

      {alerts.length === 0 ? (
        <EmptyState title={t.alerts.emptyTitle} hint={t.alerts.emptyHint} />
      ) : (
        <ul className="as-alert-list">
          {alerts.map((alert) => (
            <li key={alert.alertId} className="as-card">
              <div style={{ alignItems: "center", display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
                <Link href={`/companies/${alert.ticker}`} className="as-alert-ticker">
                  {alert.ticker}
                </Link>
                {alert.companyName ? <span className="as-meta">{alert.companyName}</span> : null}
                {alert.alphaScore !== null ? (
                  <strong>
                    Alpha Score {alert.alphaScore}
                    {alert.scoreDelta !== null ? (
                      <span className="as-meta" style={{ marginLeft: "0.4rem" }}>
                        {t.alerts.vsPrior(formatDelta(alert.scoreDelta))}
                      </span>
                    ) : null}
                  </strong>
                ) : null}
                <span className="as-badge as-badge-neutral">{formatDate(alert.alertDate)}</span>
                {alert.approved && alert.sentAt ? (
                  <span className="as-badge as-badge-positive">{t.alerts.sent(formatDate(alert.sentAt))}</span>
                ) : (
                  <span className="as-badge as-badge-negative">{t.alerts.pending}</span>
                )}
              </div>
              <p className="as-card-title" style={{ margin: "0.5rem 0 0.3rem" }}>
                {locale === "zh" ? alert.briefZh : alert.briefEn}
              </p>
              <p className="as-meta" style={{ margin: "0.4rem 0 0" }}>
                {t.alerts.cited}{" "}
                {alert.signalIds.map((id, index) => (
                  <span key={id}>
                    {index > 0 && ", "}
                    <code>{id}</code>
                  </span>
                ))}
              </p>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
