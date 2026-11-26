import Link from "next/link";

import { DirectionBadge, StrengthBadge } from "@/components/badges";
import { EmptyState } from "@/components/empty-state";
import { FixtureNotice } from "@/components/fixture-notice";
import { CATEGORIES } from "@/lib/categories";
import { getScreenerData } from "@/lib/data";
import { formatDelta } from "@/lib/format";
import { getI18n } from "@/lib/i18n";
import {
  applyScreenerFilters,
  buildScreenerRows,
  hasActiveScreenerFilters,
  parseScreenerFilters,
  screenerFilterQuery,
  sortScreenerRows,
} from "@/lib/screener";
import { THEMES, themeLabel } from "@/lib/themes";

export const dynamic = "force-dynamic";

export async function generateMetadata() {
  const { t } = await getI18n();
  return { title: t.meta.screener };
}

const STRENGTH_OPTIONS = [1, 2, 3, 4, 5] as const;

/**
 * The §7.2 Screener — five filter conditions over the covered universe,
 * resolved in memory from the same store/fixture data every module loads.
 * Plain GET form + server-side filtering, exactly like /feed.
 */
export default async function ScreenerPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { locale, t } = await getI18n();
  const filters = parseScreenerFilters(await searchParams);
  const { companies, signals, alerts, fixture } = await getScreenerData();
  const results = sortScreenerRows(
    applyScreenerFilters(buildScreenerRows(companies, signals, alerts), filters),
  );
  const exportHref = `/screener/export${screenerFilterQuery(filters) ? `?${screenerFilterQuery(filters)}` : ""}`;

  return (
    <>
      <h1 className="as-page-title">{t.screener.title}</h1>
      <p className="as-page-sub">{t.screener.sub}</p>
      {fixture ? <FixtureNotice t={t} /> : null}

      <form className="as-filters" method="get" action="/screener">
        <label>
          {t.screener.theme}
          <select name="theme" defaultValue={filters.theme ?? ""}>
            <option value="">{t.screener.allThemes}</option>
            {THEMES.map((theme) => (
              <option key={theme.id} value={theme.id}>
                {locale === "zh" ? theme.labelZh : theme.labelEn}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t.screener.category}
          <select name="category" defaultValue={filters.category ?? ""}>
            <option value="">{t.screener.allCategories}</option>
            {CATEGORIES.map((category) => (
              <option key={category.id} value={category.id}>
                {locale === "zh" ? category.labelZh : category.labelEn}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t.screener.strengthRange}
          <span className="as-range-pair">
            <select name="minStrength" defaultValue={filters.minStrength ?? ""} aria-label={t.screener.strengthRange}>
              <option value="">{t.screener.any}</option>
              {STRENGTH_OPTIONS.map((strength) => (
                <option key={strength} value={strength}>
                  ≥ {strength}
                </option>
              ))}
            </select>
            <select name="maxStrength" defaultValue={filters.maxStrength ?? ""} aria-label={t.screener.strengthRange}>
              <option value="">{t.screener.any}</option>
              {STRENGTH_OPTIONS.map((strength) => (
                <option key={strength} value={strength}>
                  ≤ {strength}
                </option>
              ))}
            </select>
          </span>
        </label>
        <label>
          {t.screener.scoreRange}
          <span className="as-range-pair">
            <input
              type="number"
              name="minScore"
              min={0}
              max={100}
              defaultValue={filters.minScore ?? ""}
              aria-label={t.screener.scoreRange}
            />
            <input
              type="number"
              name="maxScore"
              min={0}
              max={100}
              defaultValue={filters.maxScore ?? ""}
              aria-label={t.screener.scoreRange}
            />
          </span>
        </label>
        <label>
          {t.screener.sensitivityLabel}
          <select name="sensitivity" defaultValue={filters.sensitivity ?? ""}>
            <option value="">{t.screener.anySensitivity}</option>
            <option value="high">{t.screener.sensitivity.high}</option>
            <option value="medium">{t.screener.sensitivity.medium}</option>
            <option value="low">{t.screener.sensitivity.low}</option>
          </select>
        </label>
        <button type="submit">{t.screener.apply}</button>
        {hasActiveScreenerFilters(filters) ? (
          <a href="/screener">{t.screener.clear}</a>
        ) : null}
      </form>

      {results.length === 0 ? (
        <EmptyState title={t.screener.emptyTitle} hint={t.screener.emptyHint} />
      ) : (
        <>
          <p className="as-export-row">
            <a className="as-export-link" href={exportHref}>
              {t.screener.export}
            </a>
          </p>
          <div className="as-table-wrap">
            <table className="as-table">
              <thead>
                <tr>
                  <th scope="col">{t.screener.colTicker}</th>
                  <th scope="col">{t.screener.colCompany}</th>
                  <th scope="col">{t.screener.colThemes}</th>
                  <th scope="col">{t.screener.colCategory}</th>
                  <th scope="col">{t.screener.colLatestSignal}</th>
                  <th scope="col">{t.screener.colScore}</th>
                  <th scope="col">{t.screener.colSensitivity}</th>
                </tr>
              </thead>
              <tbody>
                {results.map((row) => (
                  <tr key={row.ticker} data-ticker={row.ticker}>
                    <td>
                      <Link href={`/companies/${row.ticker}`} className="as-alert-ticker">
                        {row.ticker}
                      </Link>
                    </td>
                    <td>{row.name}</td>
                    <td>
                      {row.themes.map((theme) => (
                        <span key={theme} className="as-badge as-badge-theme">
                          {themeLabel(theme, locale)}
                        </span>
                      ))}
                    </td>
                    <td>
                      {t.universeCategories[row.category] ?? row.category}
                    </td>
                    <td>
                      {row.latestSignal ? (
                        <span className="as-latest-signal">
                          <DirectionBadge direction={row.latestSignal.direction} t={t} />
                          <StrengthBadge strength={row.latestSignal.strength} t={t} />
                          <code className="as-signal-id">{row.latestSignal.signalId}</code>
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>
                      {row.alphaScore !== null ? (
                        <>
                          {row.alphaScore}
                          {row.scoreDelta !== null ? (
                            <span className="as-score-delta">{formatDelta(row.scoreDelta)}</span>
                          ) : null}
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>
                      {row.rateSensitivity
                        ? t.screener.sensitivity[row.rateSensitivity]
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}
