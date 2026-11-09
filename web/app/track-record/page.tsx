import { DirectionBadge } from "@/components/badges";
import { EmptyState } from "@/components/empty-state";
import { FixtureNotice } from "@/components/fixture-notice";
import { Provenance } from "@/components/provenance";
import { getTrackRecord } from "@/lib/data";
import { formatDate } from "@/lib/format";

export const dynamic = "force-dynamic";

export const metadata = { title: "Track Record · AlphaStock" };

/**
 * Attested history — every published signal with its hash and batch anchor,
 * including losing (negative-direction) signals. Nothing here can be edited:
 * the underlying tables are append-only and this page mutates nothing.
 */
export default async function TrackRecordPage() {
  const { attestations, signals, fixture } = await getTrackRecord();
  const batchBySignalId = new Map(
    attestations.flatMap((batch) => batch.signalIds.map((id) => [id, batch.batchId])),
  );
  const negativeCount = signals.filter((s) => s.direction === "negative").length;

  return (
    <>
      <h1 className="as-page-title">Track Record · 存证记录</h1>
      <p className="as-page-sub">
        Every published signal is hash-attested on publication and never edited afterwards —
        including the losing calls shown here.{" "}
        <span lang="zh-Hans">所有已发布信号在发布时生成哈希存证，事后不可修改——包括做错的信号。</span>
      </p>
      {fixture ? <FixtureNotice /> : null}

      <section aria-label="attestation batches" style={{ margin: "1rem 0 1.5rem" }}>
        <h2 className="as-section-title">Attestation batches · 存证批次</h2>
        {attestations.length === 0 ? (
          <EmptyState
            title="No attested batches yet"
            hint="Batches appear here after the weekly attestation job anchors its first run."
          />
        ) : (
          <table className="as-table">
            <thead>
              <tr>
                <th scope="col">Batch</th>
                <th scope="col">Batch hash (SHA-256)</th>
                <th scope="col">Signals</th>
                <th scope="col">Anchored</th>
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
        <h2 className="as-section-title">Published signals · 已发布信号</h2>
        <p className="as-meta" style={{ margin: "0.2rem 0 0.8rem" }}>
          {signals.length} attested signals · {negativeCount} negative-direction (losing).
          Signals score against sector benchmarks when their horizon expires — the record
          here is the input to that tally.{" "}
          <span lang="zh-Hans">信号到期后对照板块基准计算命中率。</span>
        </p>
        {signals.length === 0 ? (
          <EmptyState
            title="No published signals yet"
            hint="The track record fills in as the daily pipeline verifies and publishes signals."
          />
        ) : (
          signals.map((signal) => (
            <article
              key={signal.signalId}
              className="as-card"
              style={{ marginBottom: "0.9rem" }}
            >
              <div
                style={{
                  alignItems: "center",
                  display: "flex",
                  flexWrap: "wrap",
                  gap: "0.4rem",
                }}
              >
                <DirectionBadge direction={signal.direction} />
                <span className="as-meta">
                  {formatDate(signal.publishedAt)} · {signal.signalId}
                  {batchBySignalId.has(signal.signalId)
                    ? ` · anchored in ${batchBySignalId.get(signal.signalId)}`
                    : ""}
                </span>
              </div>
              <p className="as-card-title" style={{ margin: "0.5rem 0 0.3rem" }}>
                {signal.summaryEn}
              </p>
              <p className="as-summary-zh" lang="zh-Hans">
                {signal.summaryZh}
              </p>
              <p className="as-hash as-meta" style={{ margin: "0.4rem 0 0" }}>
                <code>{signal.hash}</code>
              </p>
              <Provenance sources={signal.sources} invalidation={signal.invalidation} />
            </article>
          ))
        )}
      </section>
    </>
  );
}
