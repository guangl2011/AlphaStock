import { FixtureNotice } from "@/components/fixture-notice";
import { correlationOf, fmt, getFactors, heatColor } from "@/lib/factors";
import { getI18n } from "@/lib/i18n";

export const dynamic = "force-dynamic";

export async function generateMetadata() {
  const { t } = await getI18n();
  return { title: t.meta.factors };
}

const ORDER = ["AS-100", "AS-MCG100", "AS-MCB100"];
const LEADER_FACTORS = ["MKT", "GRID", "NUKE", "ROBO", "UTIL"];

/**
 * Factors library — the Correlation & Factor-Relationship Model on the
 * research frame: factor catalog, index exposures (OLS β, t, rolling
 * correlations), the full correlation matrix, and the constituents that
 * co-move hardest with each factor.
 */
export default async function FactorsPage() {
  const { t } = await getI18n();
  const f = t.factors;
  const data = await getFactors();
  const indexIds = [...data.indexIds].sort((a, b) => ORDER.indexOf(a) - ORDER.indexOf(b));
  const factorNames = data.catalog.map((c) => c.factor);
  const days = Math.max(...Object.values(data.exposures).map((e) => e.regressionDays));
  const heatSeries = [...indexIds, ...factorNames];
  const full = data.correlations.full ?? {};

  return (
    <>
      <h1 className="as-page-title">{f.title}</h1>
      <p className="as-page-sub">{f.sub(data.source, days)}</p>
      <FixtureNotice t={t} />

      <section aria-label={f.catalogTitle} style={{ marginBottom: "2rem" }}>
        <h2 className="as-section-title">{f.catalogTitle}</h2>
        <div className="as-table-wrap">
          <table className="as-table">
            <thead>
              <tr>
                <th scope="col">{f.colFactor}</th>
                <th scope="col">{f.colConstruction}</th>
                <th scope="col">{f.colMeaning}</th>
                <th scope="col">{f.colRegression}</th>
              </tr>
            </thead>
            <tbody>
              {data.catalog.map((c) => (
                <tr key={c.factor}>
                  <td>
                    <strong>{c.factor}</strong>
                  </td>
                  <td>
                    <code>
                      {c.kind === "spread" ? `${c.proxies[0]} − ${c.proxies[1]}` : c.proxies[0]}
                    </code>
                  </td>
                  <td className="as-wrap-cell">{f.meanings[c.factor] ?? ""}</td>
                  <td>{c.inRegression ? f.yes : f.corrOnly}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section aria-label={f.exposuresTitle} style={{ marginBottom: "2rem" }}>
        <h2 className="as-section-title">{f.exposuresTitle}</h2>
        <p className="as-meta" style={{ margin: "0.2rem 0 0.8rem" }}>{f.exposuresNote}</p>
        <div className="as-factor-grid">
          {indexIds.map((id) => {
            const e = data.exposures[id];
            if (!e) return null;
            return (
              <div key={id} className="as-card" aria-label={`exposures ${id}`}>
                <h3 className="as-card-title">
                  {id} — {t.indexNames[id] ?? id}
                </h3>
                <p className="as-meta" style={{ margin: "0.2rem 0 0.5rem" }}>
                  {f.fitLine(fmt(e.rSquared, 3), e.regressionDays)}
                </p>
                <div className="as-table-wrap">
                  <table className="as-table as-table-compact">
                    <thead>
                      <tr>
                        <th scope="col">{f.colFactor}</th>
                        <th scope="col">{f.colBeta}</th>
                        <th scope="col">{f.colT}</th>
                        <th scope="col">{f.colCorrFull}</th>
                        <th scope="col">{f.colCorr63}</th>
                        <th scope="col">{f.colCorr21}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {factorNames.map((name) => {
                        const s = e.factors[name];
                        if (!s) return null;
                        return (
                          <tr key={name}>
                            <td>{name}</td>
                            <td>{fmt(s.beta)}</td>
                            <td>{fmt(s.tStat, 1)}</td>
                            <td>{fmt(s.corrFull)}</td>
                            <td>{fmt(s.corr63d)}</td>
                            <td>{fmt(s.corr21d)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <section aria-label={f.heatTitle} style={{ marginBottom: "2rem" }}>
        <h2 className="as-section-title">{f.heatTitle}</h2>
        <p className="as-meta" style={{ margin: "0.2rem 0 0.8rem" }}>{f.heatNote}</p>
        <div className="as-table-wrap">
          <table className="as-table as-heat-table">
            <thead>
              <tr>
                <th scope="col" />
                {heatSeries.map((s) => (
                  <th key={s} scope="col">{s}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {heatSeries.map((a) => (
                <tr key={a}>
                  <th scope="row">{a}</th>
                  {heatSeries.map((b) => {
                    const v = correlationOf(full, a, b);
                    return (
                      <td
                        key={b}
                        style={{ background: heatColor(v) }}
                        title={`${a} × ${b}: ${fmt(v)}`}
                      >
                        {fmt(v)}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section aria-label={f.leadersTitle} style={{ marginBottom: "2rem" }}>
        <h2 className="as-section-title">{f.leadersTitle}</h2>
        <p className="as-meta" style={{ margin: "0.2rem 0 0.8rem" }}>{f.leadersNote(5)}</p>
        {indexIds.map((id) => (
          <div key={id} style={{ marginBottom: "1.25rem" }}>
            <h3 className="as-card-title">
              {id} — {t.indexNames[id] ?? id}
            </h3>
            <div className="as-factor-grid">
              {LEADER_FACTORS.map((factor) => {
                const list = data.leaders[id]?.[factor] ?? [];
                return (
                  <div key={factor} className="as-table-wrap">
                  <table className="as-table as-table-compact">
                    <caption>{factor}</caption>
                    <thead>
                      <tr>
                        <th scope="col">{f.colRank}</th>
                        <th scope="col">{f.colTicker}</th>
                        <th scope="col">{f.colCorr}</th>
                        <th scope="col">{f.colBeta63}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {list.map((l, i) => (
                        <tr key={l.ticker}>
                          <td>{i + 1}</td>
                          <td>
                            <strong>{l.ticker}</strong>
                          </td>
                          <td>{fmt(l.corr63d)}</td>
                          <td>{fmt(l.beta63d)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </section>

      <aside className="as-research-caveats" aria-label={f.caveatsTitle}>
        <strong>{f.caveatsTitle}</strong>
        <ul>
          {f.caveats.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ul>
      </aside>
    </>
  );
}
