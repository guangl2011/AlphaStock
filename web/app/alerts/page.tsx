import Link from "next/link";

import { EmptyState } from "@/components/empty-state";
import { FixtureNotice } from "@/components/fixture-notice";
import { getAlerts } from "@/lib/data";
import { formatDate, formatDelta } from "@/lib/format";

export const dynamic = "force-dynamic";

export const metadata = { title: "Weekly Alerts · AlphaStock" };

/**
 * Weekly alert history — the Monday top-10 briefs after verification and the
 * research owner's approval gate. Rows awaiting approval show as pending: they
 * were never sent, and the history says so rather than hiding them.
 */
export default async function AlertsPage() {
  const { alerts, fixture } = await getAlerts();

  return (
    <>
      <h1 className="as-page-title">Weekly Alerts · 每周警报</h1>
      <p className="as-page-sub">
        Monday briefs for the ten highest-scoring covered names, published only after the
        verification pass and the research owner&apos;s approval.{" "}
        <span lang="zh-Hans">
          每周一为评分最高的十只覆盖股票发布简报，须经核验与研究负责人批准后发出。
        </span>
      </p>
      {fixture ? <FixtureNotice /> : null}

      {alerts.length === 0 ? (
        <EmptyState
          title="No alerts yet"
          hint="The first weekly alert run has not published. Briefs appear here after approval."
        />
      ) : (
        <ul className="as-alert-list">
          {alerts.map((alert) => (
            <li key={alert.alertId} className="as-card">
              <div
                style={{
                  alignItems: "center",
                  display: "flex",
                  flexWrap: "wrap",
                  gap: "0.4rem",
                }}
              >
                <Link href={`/companies/${alert.ticker}`} className="as-alert-ticker">
                  {alert.ticker}
                </Link>
                {alert.companyName ? (
                  <span className="as-meta">{alert.companyName}</span>
                ) : null}
                {alert.alphaScore !== null ? (
                  <strong>
                    Alpha Score {alert.alphaScore}
                    {alert.scoreDelta !== null ? (
                      <span className="as-meta" style={{ marginLeft: "0.4rem" }}>
                        {formatDelta(alert.scoreDelta)} vs prior week
                      </span>
                    ) : null}
                  </strong>
                ) : null}
                <span className="as-badge as-badge-neutral">{formatDate(alert.alertDate)}</span>
                {alert.approved && alert.sentAt ? (
                  <span className="as-badge as-badge-positive">
                    sent {formatDate(alert.sentAt)}
                  </span>
                ) : (
                  <span className="as-badge as-badge-negative">
                    pending owner approval · 未发送
                  </span>
                )}
              </div>
              <p className="as-card-title" style={{ margin: "0.5rem 0 0.3rem" }}>
                {alert.briefEn}
              </p>
              <p className="as-summary-zh" lang="zh-Hans">
                {alert.briefZh}
              </p>
              <p className="as-meta" style={{ margin: "0.4rem 0 0" }}>
                Signals cited:{" "}
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
