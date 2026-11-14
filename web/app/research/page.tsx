import { ResearchChart } from "@/components/research-chart";
import { FixtureNotice } from "@/components/fixture-notice";
import type { ResearchIndexView, SeriesStats } from "@/lib/research";
import { chartSeries, getResearch, pct, pctAbs } from "@/lib/research";

export const dynamic = "force-dynamic";

export const metadata = { title: "Research backfill · AlphaStock" };

const INDEX_NAMES: Record<string, { en: string; zh: string }> = {
  "AS-100": { en: "Mixed universe equal-weight", zh: "混合宇宙等权指数" },
  "AS-MC100": { en: "Large-Cap 100 equal-weight", zh: "大盘 100 等权指数" },
};

/**
 * Three-year research frame (2024-01-02 → latest close): both universes as
 * constituted today, run through the production equal-weight engine with
 * point-in-time entry for late listings, against ETF benchmark proxies.
 * Caveats lead the page — this is a current-constituent backtest.
 */
export default async function ResearchPage() {
  const data = await getResearch();
  const years = Object.keys(data.indices[0]?.stats.calendarReturns ?? {}).sort();

  return (
    <>
      <h1 className="as-page-title">Research backfill · 三年回溯研究</h1>
      <p className="as-page-sub">
        AS-100 and AS-MC100 from {data.frame.start} to {data.frame.end}, base 1000 on the
        first trading day of 2024, production methodology v{data.methodologyVersion}{" "}
        mechanics. Internal research view — not a published index value.{" "}
        <span lang="zh-Hans">
          以 2024 年首个交易日为基期 1000 回溯两大成分宇宙；仅供内部研究，非正式发布指数。
        </span>
      </p>
      <FixtureNotice />

      <aside className="as-research-caveats" aria-label="Research caveats">
        <strong>Read before the numbers · 阅读须知</strong>
        <ul>
          {data.caveats.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ul>
        {data.universeOverlap.length > 0 ? (
          <p>
            Universe files in this repo share {data.universeOverlap.length} tickers, so the two
            series are not yet independent — the Nov 9 disjoint 20/60/20 Mixed list has not
            landed. · <span lang="zh-Hans">当前仓库两大宇宙重叠 {data.universeOverlap.length} 只。</span>
          </p>
        ) : null}
        <p className="as-meta">Source: {data.source}</p>
      </aside>

      {data.indices.map((index) => (
        <IndexResearch key={index.indexId} index={index} years={years} />
      ))}
    </>
  );
}

function IndexResearch({ index, years }: { index: ResearchIndexView; years: string[] }) {
  const name = INDEX_NAMES[index.indexId] ?? { en: index.indexId, zh: index.indexId };
  const { dates, series } = chartSeries(index);
  const spy = index.benchmarks.SPY;
  const rows: { key: string; label: string; stats: SeriesStats; strong?: boolean }[] = [
    { key: index.indexId, label: index.indexId, stats: index.stats, strong: true },
    ...[...index.broadBenchmarks, ...index.thematicBenchmarks]
      .filter((p) => index.benchmarks[p])
      .map((p) => ({ key: p, label: `${p} — ${index.benchmarks[p].name}`, stats: index.benchmarks[p].stats })),
  ];

  return (
    <section aria-label={`research ${index.indexId}`} style={{ marginBottom: "2.5rem" }}>
      <h2 className="as-section-title">
        {index.indexId} — {name.en} · <span lang="zh-Hans">{name.zh}</span>
      </h2>
      <p className="as-meta" style={{ margin: "0.2rem 0 0.8rem" }}>
        {index.constituentsAtBase} constituents at base → {index.constituentsAtEnd} after
        late-listing entries · {index.eventCounts.rebalance ?? 0} quarterly rebalances ·{" "}
        {index.eventCounts.corporate_action ?? 0} corporate-action adjustments
      </p>

      <div className="as-kpis">
        <Kpi label="Total return · 累计" value={pct(index.stats.totalReturn)}
          sub={spy ? `SPY ${pct(spy.stats.totalReturn)}` : undefined} />
        <Kpi label="CAGR · 年化" value={pct(index.stats.cagr)}
          sub={spy ? `SPY ${pct(spy.stats.cagr)}` : undefined} />
        <Kpi label="Volatility · 波动率" value={pctAbs(index.stats.annVol)}
          sub={spy ? `SPY ${pctAbs(spy.stats.annVol)}` : undefined} />
        <Kpi label="Max drawdown · 最大回撤" value={pct(index.stats.maxDrawdown)}
          sub={`${index.stats.maxDrawdownPeak} → ${index.stats.maxDrawdownTrough}`} />
      </div>

      <ResearchChart
        dates={dates}
        series={series}
        label={`${index.indexId} and benchmark proxies, base 1000 at ${index.baseDate}`}
      />

      <div className="as-table-wrap">
        <table className="as-table" style={{ marginTop: "1rem" }}>
          <caption>Risk & return, {index.stats.start} → {index.stats.end} · 风险与收益</caption>
          <thead>
            <tr>
              <th scope="col">Series · 序列</th>
              <th scope="col">Total</th>
              <th scope="col">CAGR</th>
              <th scope="col">Vol</th>
              <th scope="col">CAGR/Vol</th>
              <th scope="col">Max DD</th>
              {years.map((y) => (
                <th key={y} scope="col">{y}{y === years[years.length - 1] ? " YTD" : ""}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key}>
                <td>{r.strong ? <strong>{r.label}</strong> : r.label}</td>
                <td>{pct(r.stats.totalReturn)}</td>
                <td>{pct(r.stats.cagr)}</td>
                <td>{pctAbs(r.stats.annVol)}</td>
                <td>{r.stats.returnToVol.toFixed(2)}</td>
                <td>{pct(r.stats.maxDrawdown)}</td>
                {years.map((y) => (
                  <td key={y}>
                    {r.stats.calendarReturns[y] === undefined ? "—" : pct(r.stats.calendarReturns[y])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="as-table-wrap">
        <table className="as-table" style={{ marginTop: "1rem" }}>
          <caption>
            Category sub-indices (equal weight, same entry rules) · 分类子指数
          </caption>
          <thead>
            <tr>
              <th scope="col">Category · 类别</th>
              <th scope="col">Names</th>
              <th scope="col">Total</th>
              <th scope="col">CAGR</th>
              <th scope="col">Vol</th>
              <th scope="col">Max DD</th>
            </tr>
          </thead>
          <tbody>
            {index.categories.map((c) => (
              <tr key={c.category}>
                <td>{c.category}</td>
                <td>{c.constituents}</td>
                <td>{pct(c.totalReturn)}</td>
                <td>{pct(c.cagr)}</td>
                <td>{pctAbs(c.annVol)}</td>
                <td>{pct(c.maxDrawdown)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="as-meta" style={{ marginTop: "0.75rem" }}>
        Late listings entered at the first quarterly rebalance after regular-way listing:{" "}
        {index.entries.length > 0
          ? index.entries.map((e) => `${e.ticker} (${e.date})`).join(", ")
          : "none"}
        .
        {index.excluded.length > 0
          ? ` Excluded: ${index.excluded.map((e) => e.ticker).join(", ")} — no EOD history, documented in the index exclusion log.`
          : ""}
      </p>
    </section>
  );
}

function Kpi({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="as-kpi">
      <span className="as-kpi-label">{label}</span>
      <span className="as-kpi-value">{value}</span>
      {sub ? <span className="as-kpi-sub">{sub}</span> : null}
    </div>
  );
}
