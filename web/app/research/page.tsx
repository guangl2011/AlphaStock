import { EnglishOnly, FixtureNotice } from "@/components/fixture-notice";
import { ResearchChart } from "@/components/research-chart";
import { getI18n, type Locale } from "@/lib/i18n";
import type { Messages } from "@/lib/messages";
import type { ResearchIndexView, SeriesStats } from "@/lib/research";
import { chartSeries, getResearch, pct, pctAbs } from "@/lib/research";

export const dynamic = "force-dynamic";

export async function generateMetadata() {
  const { t } = await getI18n();
  return { title: t.meta.research };
}

const ORDER = ["AS-100", "AS-MCG100", "AS-MCB100"];

/**
 * Three-year research frame (2024-01-02 → latest close): all three universes
 * as constituted today, run through the production equal-weight engine with
 * point-in-time entry for late listings, against ETF benchmark proxies.
 * Caveats lead the page — this is a current-constituent backtest.
 */
export default async function ResearchPage() {
  const { locale, t } = await getI18n();
  const data = await getResearch();
  const indices = [...data.indices].sort(
    (a, b) => ORDER.indexOf(a.indexId) - ORDER.indexOf(b.indexId),
  );
  const years = Object.keys(indices[0]?.stats.calendarReturns ?? {}).sort();
  const caveats = locale === "zh" ? data.caveatsZh : data.caveats;

  return (
    <>
      <h1 className="as-page-title">{t.research.title}</h1>
      <p className="as-page-sub">
        {t.research.sub(data.frame.start, data.frame.end, data.methodologyVersion)}
      </p>
      <FixtureNotice t={t} />

      <aside className="as-research-caveats" aria-label={t.research.caveatsTitle}>
        <strong>{t.research.caveatsTitle}</strong>
        <ul>
          {caveats.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ul>
        <p>
          {data.universeOverlap.length > 0
            ? t.research.overlapWarn(data.universeOverlap.length)
            : t.research.sharedNote(data.mixedCapShared)}
        </p>
        <p className="as-meta">{t.research.source(data.source)}</p>
      </aside>

      {indices.map((index) => (
        <IndexResearch key={index.indexId} index={index} years={years} t={t} locale={locale} />
      ))}
    </>
  );
}

function IndexResearch({
  index,
  years,
  t,
  locale,
}: {
  index: ResearchIndexView;
  years: string[];
  t: Messages;
  locale: Locale;
}) {
  const r = t.research;
  const { dates, series } = chartSeries(index);
  const spy = index.benchmarks.SPY;
  const rows: { key: string; label: string; stats: SeriesStats; strong?: boolean }[] = [
    { key: index.indexId, label: index.indexId, stats: index.stats, strong: true },
    ...[...index.broadBenchmarks, ...index.thematicBenchmarks]
      .filter((p) => index.benchmarks[p])
      .map((p) => ({
        key: p,
        label: `${p} — ${t.proxyNames[p] ?? index.benchmarks[p].name}`,
        stats: index.benchmarks[p].stats,
      })),
  ];

  return (
    <section aria-label={`research ${index.indexId}`} style={{ marginBottom: "2.5rem" }}>
      <h2 className="as-section-title">
        {index.indexId} — {t.indexNames[index.indexId] ?? index.indexId}
      </h2>
      <p className="as-meta" style={{ margin: "0.2rem 0 0" }}>
        {t.indexDescriptions[index.indexId] ?? ""}
      </p>
      <p className="as-meta" style={{ margin: "0.2rem 0 0.8rem" }}>
        {r.metaLine(
          index.constituentsAtBase,
          index.constituentsAtEnd,
          index.eventCounts.rebalance ?? 0,
          index.eventCounts.corporate_action ?? 0,
        )}
      </p>

      <div className="as-kpis">
        <Kpi label={r.totalReturn} value={pct(index.stats.totalReturn)}
          sub={spy ? `SPY ${pct(spy.stats.totalReturn)}` : undefined} />
        <Kpi label={r.cagr} value={pct(index.stats.cagr)}
          sub={spy ? `SPY ${pct(spy.stats.cagr)}` : undefined} />
        <Kpi label={r.vol} value={pctAbs(index.stats.annVol)}
          sub={spy ? `SPY ${pctAbs(spy.stats.annVol)}` : undefined} />
        <Kpi label={r.maxDd} value={pct(index.stats.maxDrawdown)}
          sub={`${index.stats.maxDrawdownPeak} → ${index.stats.maxDrawdownTrough}`} />
      </div>

      <ResearchChart dates={dates} series={series} label={r.chartLabel(index.indexId, index.baseDate)} />

      <div className="as-table-wrap">
        <table className="as-table" style={{ marginTop: "1rem" }}>
          <caption>{r.riskCaption(index.stats.start, index.stats.end)}</caption>
          <thead>
            <tr>
              <th scope="col">{r.colSeries}</th>
              <th scope="col">{r.colTotal}</th>
              <th scope="col">{r.colCagr}</th>
              <th scope="col">{r.colVol}</th>
              <th scope="col">{r.colRatio}</th>
              <th scope="col">{r.colMaxDd}</th>
              {years.map((y) => (
                <th key={y} scope="col">
                  {y}
                  {y === years[years.length - 1] ? ` ${r.ytd}` : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key}>
                <td>{row.strong ? <strong>{row.label}</strong> : row.label}</td>
                <td>{pct(row.stats.totalReturn)}</td>
                <td>{pct(row.stats.cagr)}</td>
                <td>{pctAbs(row.stats.annVol)}</td>
                <td>{row.stats.returnToVol.toFixed(2)}</td>
                <td>{pct(row.stats.maxDrawdown)}</td>
                {years.map((y) => (
                  <td key={y}>
                    {row.stats.calendarReturns[y] === undefined ? "—" : pct(row.stats.calendarReturns[y])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="as-table-wrap">
        <table className="as-table" style={{ marginTop: "1rem" }}>
          <caption>{r.catCaption}</caption>
          <thead>
            <tr>
              <th scope="col">{r.colCategory}</th>
              <th scope="col">{r.colNames}</th>
              <th scope="col">{r.colTotal}</th>
              <th scope="col">{r.colCagr}</th>
              <th scope="col">{r.colVol}</th>
              <th scope="col">{r.colMaxDd}</th>
            </tr>
          </thead>
          <tbody>
            {index.categories.map((c) => (
              <tr key={c.category}>
                <td>{t.universeCategories[c.category] ?? c.category}</td>
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

      {index.corporateEvents.length > 0 ? (
        <div className="as-table-wrap">
          <table className="as-table" style={{ marginTop: "1rem" }}>
            <caption>{r.eventsCaption}</caption>
            <thead>
              <tr>
                <th scope="col">{r.colDate}</th>
                <th scope="col">{r.colTicker}</th>
                <th scope="col">{r.colEvent}</th>
                <th scope="col">{r.colDetail}</th>
                <th scope="col">{r.colTreatment}</th>
                <th scope="col">{r.colCheck}</th>
                <th scope="col">{r.colSource}</th>
              </tr>
            </thead>
            <tbody>
              {index.corporateEvents.map((e) => (
                <tr key={`${e.ticker}-${e.date}-${e.event}`}>
                  <td>{e.date}</td>
                  <td>
                    <strong>{e.ticker}</strong>
                  </td>
                  <td>{r.eventKinds[e.event] ?? e.event}</td>
                  <td className="as-wrap-cell" lang="en">{e.detail}</td>
                  <td className="as-wrap-cell" lang="en">{e.indexTreatment}</td>
                  <td>
                    {e.status === "pending"
                      ? r.pending
                      : e.frameMove === null
                        ? "—"
                        : r.adjusted(pct(e.frameMove))}
                  </td>
                  <td>
                    <a href={e.source} target="_blank" rel="noreferrer">
                      {new URL(e.source).hostname.replace(/^www\./, "")}
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <EnglishOnly t={t} show={locale === "zh"} />
        </div>
      ) : null}

      <p className="as-meta" style={{ marginTop: "0.75rem" }}>
        {r.entries}{" "}
        {index.entries.length > 0
          ? index.entries.map((e) => `${e.ticker} (${e.date})`).join(", ")
          : r.none}
        {index.excluded.length > 0 ? r.excluded(index.excluded.map((e) => e.ticker).join(", ")) : ""}
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
