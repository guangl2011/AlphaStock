import { DirectionBadge } from "@/components/badges";
import { EmptyState } from "@/components/empty-state";
import { FixtureNotice } from "@/components/fixture-notice";
import { Provenance } from "@/components/provenance";
import { getTrackRecord } from "@/lib/data";
import { formatDate } from "@/lib/format";
import { getI18n } from "@/lib/i18n";

export const dynamic = "force-dynamic";

export async function generateMetadata() {
  const { t } = await getI18n();
  return { title: t.meta.trackRecord };
}

/**
 * Attested history — every published signal with its hash and batch anchor,
 * including losing (negative-direction) signals. Nothing here can be edited:
 * the underlying tables are append-only and this page mutates nothing.
 */
export default async function TrackRecordPage() {
  const { locale, t } = await getI18n();
  const { attestations, signals, fixture } = await getTrackRecord();
  const batchBySignalId = new Map(
    attestations.flatMap((batch) => batch.signalIds.map((id) => [id, batch.batchId])),
  );
  const negativeCount = signals.filter((s) => s.direction === "negative").length;

  return (
    <>
      <h1 className="as-page-title">{t.trackRecord.title}</h1>
      <p className="as-page-sub">{t.trackRecord.sub}</p>
      {fixture ? <FixtureNotice t={t} /> : null}

      <section aria-label="attestation batches" style={{ margin: "1rem 0 1.5rem" }}>
        <h2 className="as-section-title">{t.trackRecord.batches}</h2>
        {attestations.length === 0 ? (
          <EmptyState title={t.trackRecord.noBatchesTitle} hint={t.trackRecord.noBatchesHint} />
        ) : (
          <table className="as-table">
            <thead>
              <tr>
                <th scope="col">{t.trackRecord.colBatch}</th>
                <th scope="col">{t.trackRecord.colHash}</th>
                <th scope="col">{t.trackRecord.colSignals}</th>
                <th scope="col">{t.trackRecord.colAnchored}</th>
              </tr>
            </thead>
            <tbody>
              {attestations.map((batch) => (
                <tr key={batch.batchId}>
                  <td>{batch.batchId}</td>
                  <td className="as-hash">
                    <code>{batch.batchHash.slice(0, 20)}…</code>
                  </td>
                  <td>{batch.signalCount}</td>
                  <td>{formatDate(batch.anchoredAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section aria-label="attested signals">
        <h2 className="as-section-title">{t.trackRecord.published}</h2>
        <p className="as-meta" style={{ margin: "0.2rem 0 0.8rem" }}>
          {t.trackRecord.summary(signals.length, negativeCount)}
        </p>
        {signals.length === 0 ? (
          <EmptyState title={t.trackRecord.noSignalsTitle} hint={t.trackRecord.noSignalsHint} />
        ) : (
          signals.map((signal) => (
            <article key={signal.signalId} className="as-card" style={{ marginBottom: "0.9rem" }}>
              <div style={{ alignItems: "center", display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
                <DirectionBadge direction={signal.direction} t={t} />
                <span className="as-meta">
                  {formatDate(signal.publishedAt)} · {signal.signalId}
                  {batchBySignalId.has(signal.signalId)
                    ? t.trackRecord.anchoredIn(batchBySignalId.get(signal.signalId) ?? "")
                    : ""}
                </span>
              </div>
              <p className="as-card-title" style={{ margin: "0.5rem 0 0.3rem" }}>
                {locale === "zh" ? signal.summaryZh : signal.summaryEn}
              </p>
              <p className="as-hash as-meta" style={{ margin: "0.4rem 0 0" }}>
                <code>{signal.hash}</code>
              </p>
              <Provenance sources={signal.sources} invalidation={signal.invalidation} t={t} locale={locale} />
            </article>
          ))
        )}
      </section>
    </>
  );
}
