import { FixtureNotice } from "@/components/fixture-notice";
import { Sparkline } from "@/components/sparkline";
import type { IndexView } from "@/lib/indices";
import {
  formatLevel,
  formatReturnPct,
  getIndices,
  levelDates,
  sinceBaseReturn,
  sparklinePoints,
} from "@/lib/indices";

export const dynamic = "force-dynamic";

export const metadata = { title: "Indices · AlphaStock" };

/** Bilingual name per index; the ids themselves are the product's own terms. */
const INDEX_NAMES: Record<string, { en: string; zh: string }> = {
  "AS-100": { en: "Mixed universe equal-weight", zh: "混合宇宙等权指数" },
  "AS-MC100": { en: "Large-Cap 100 equal-weight", zh: "大盘 100 等权指数" },
};

/**
 * Index comparison view — AS-100 / AS-MC100 daily levels vs ETF benchmark
 * proxies, all rebased to 1000 at each index's base date by the pipeline.
 * Served from the pipeline's exported artifacts; documented exclusions are
 * footnoted, never hidden.
 */
export default async function IndicesPage() {
  const { indices, proxyMapping, methodologyVersion } = await getIndices();

  const proxyList = Object.entries(proxyMapping)
    .map(([proxy, name]) => `${proxy} (${name.replace(" (ETF proxy)", "")})`)
    .join(", ");

  return (
    <>
      <h1 className="as-page-title">Indices · 指数对比</h1>
      <p className="as-page-sub">
        Equal-weight price-return indices, base 1000 at their base-date close,
        compared against ETF benchmark proxies (licensed index values are never
        fetched): {proxyList}. Methodology v{methodologyVersion}.{" "}
        <span lang="zh-Hans">
          等权价格指数，基期收盘点位为 1000，对比 ETF 基准代理；基准代理并非官方指数值。
        </span>
      </p>
      <FixtureNotice />

      {indices.map((index) => (
        <IndexSection key={index.indexId} index={index} />
      ))}
    </>
  );
}

function IndexSection({ index }: { index: IndexView }) {
  const name = INDEX_NAMES[index.indexId] ?? { en: index.indexId, zh: index.indexId };
  const dates = levelDates(index.levels);
  const latest = dates.length > 0 ? dates[dates.length - 1] : null;
  const latestLevel = latest !== null ? index.levels[latest] : NaN;
  const indexReturn = sinceBaseReturn(index.levels);

  return (
    <section aria-label={`index ${index.indexId}`} style={{ marginBottom: "2rem" }}>
      <h2 className="as-section-title">
        {index.indexId} — {name.en} · <span lang="zh-Hans">{name.zh}</span>
      </h2>
      <p className="as-meta" style={{ margin: "0.2rem 0 0.8rem" }}>
        Base 1000 at {index.baseDate} close · {index.constituentCount} constituents ·
        latest {latest ?? "—"}
        {" · "}
        <span lang="zh-Hans">
          基期 {index.baseDate} · 成分股 {index.constituentCount} 只 · 最新 {latest ?? "—"}
        </span>
      </p>

      <Sparkline
        points={sparklinePoints(index.levels, 240, 48, 4)}
        label={`${index.indexId} daily levels since ${index.baseDate}`}
      />

      <table className="as-table" style={{ marginTop: "0.75rem" }}>
        <thead>
          <tr>
            <th scope="col">Series · 序列</th>
            <th scope="col">Latest level · 最新点位</th>
            <th scope="col">Since base · 基期以来</th>
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
            const benchmarkReturn = sinceBaseReturn(benchmark.levels);
            const benchmarkLatest =
              latest !== null ? benchmark.levels[latest] : undefined;
            return (
              <tr key={benchmark.proxy}>
                <td>
                  {benchmark.proxy} — {benchmark.name}
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
          {index.excluded.map((e) => e.ticker).join(", ")} is excluded from{" "}
          {index.indexId} — documented Tiingo EOD data gap, recorded in the index
          exclusion log and pending the monthly review (not an error). ·{" "}
          <span lang="zh-Hans">
            {index.excluded.map((e) => e.ticker).join("、")} 因 Tiingo EOD
            数据缺口暂不计入，已在指数排除记录中说明，待月度评审。
          </span>
        </p>
      ) : null}
    </section>
  );
}
