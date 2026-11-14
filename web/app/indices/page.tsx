import { FixtureNotice } from "@/components/fixture-notice";
import { Sparkline } from "@/components/sparkline";
import { getI18n } from "@/lib/i18n";
import type { IndexView } from "@/lib/indices";
import {
  formatLevel,
  formatReturnPct,
  getIndices,
  levelDates,
  sinceBaseReturn,
  sparklinePoints,
} from "@/lib/indices";
import type { Messages } from "@/lib/messages";

export const dynamic = "force-dynamic";

export async function generateMetadata() {
  const { t } = await getI18n();
  return { title: t.meta.indices };
}

/** Canonical display order: Large-Cap first, then the two mixed-cap weightings. */
const ORDER = ["AS-100", "AS-MCG100", "AS-MCB100"];

/**
 * Index comparison view — AS-100 / AS-MCG100 / AS-MCB100 daily levels vs ETF
 * benchmark proxies, all rebased to 1000 at each index's base date by the
 * pipeline. Served from the pipeline's exported artifacts; documented
 * exclusions are footnoted, never hidden.
 */
export default async function IndicesPage() {
  const { t } = await getI18n();
  const { indices, proxyMapping, methodologyVersion } = await getIndices();
  const sorted = [...indices].sort((a, b) => ORDER.indexOf(a.indexId) - ORDER.indexOf(b.indexId));
  const proxyList = Object.keys(proxyMapping).sort().join(", ");

  return (
    <>
      <h1 className="as-page-title">{t.indices.title}</h1>
      <p className="as-page-sub">{t.indices.sub(proxyList, methodologyVersion)}</p>
      <FixtureNotice t={t} />
      {sorted.map((index) => (
        <IndexSection key={index.indexId} index={index} t={t} />
      ))}
    </>
  );
}

function IndexSection({ index, t }: { index: IndexView; t: Messages }) {
  const dates = levelDates(index.levels);
  const latest = dates.length > 0 ? dates[dates.length - 1] : null;
  const latestLevel = latest !== null ? index.levels[latest] : NaN;
  const indexReturn = sinceBaseReturn(index.levels, index.baseDate);

  return (
    <section aria-label={`index ${index.indexId}`} style={{ marginBottom: "2rem" }}>
      <h2 className="as-section-title">
        {index.indexId} — {t.indexNames[index.indexId] ?? index.indexId}
      </h2>
      <p className="as-meta" style={{ margin: "0.2rem 0 0" }}>
        {t.indexDescriptions[index.indexId] ?? ""}
      </p>
      <p className="as-meta" style={{ margin: "0.2rem 0 0.8rem" }}>
        {t.indices.baseLine(index.baseDate, index.constituentCount, latest ?? "—")}
      </p>

      <Sparkline
        points={sparklinePoints(index.levels, 240, 48, 4)}
        label={t.indices.sparkLabel(index.indexId, index.baseDate)}
      />

      <table className="as-table" style={{ marginTop: "0.75rem" }}>
        <thead>
          <tr>
            <th scope="col">{t.indices.colSeries}</th>
            <th scope="col">{t.indices.colLatest}</th>
            <th scope="col">{t.indices.colSinceBase}</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>
              <strong>{index.indexId}</strong>
            </td>
            <td>{formatLevel(latestLevel)}</td>
            <td>{formatReturnPct(indexReturn)}</td>
          </tr>
          {index.benchmarks.map((benchmark) => {
            // Benchmarks carry pre-base dates in the export; "since base"
            // must anchor at the index's own base-date 1000, per methodology.
            const benchmarkReturn = sinceBaseReturn(benchmark.levels, index.baseDate);
            const benchmarkLatest = latest !== null ? benchmark.levels[latest] : undefined;
            return (
              <tr key={benchmark.proxy}>
                <td>
                  {benchmark.proxy} — {t.proxyNames[benchmark.proxy] ?? benchmark.name}
                </td>
                <td>{benchmarkLatest === undefined ? "—" : formatLevel(benchmarkLatest)}</td>
                <td>{formatReturnPct(benchmarkReturn)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {index.excluded.length > 0 ? (
        <p className="as-meta" style={{ marginTop: "0.5rem" }}>
          {t.indices.excluded(index.excluded.map((e) => e.ticker).join(", "), index.indexId)}
        </p>
      ) : null}
    </section>
  );
}
