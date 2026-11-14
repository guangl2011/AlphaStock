import { FixtureNotice } from "@/components/fixture-notice";
import { getThemesData } from "@/lib/data";
import {
  getAvailableQuarters,
  getAvailableYears,
  getMostRecentQuarter,
  getQuarterlyFindings,
  getYearlyFindings,
  type PeriodFindings,
  type Quarter,
} from "@/lib/findings";
import { getI18n } from "@/lib/i18n";
import type { Messages } from "@/lib/messages";
import { computeThemeHeat, themeLabel, type HeatLevel } from "@/lib/themes";

export const dynamic = "force-dynamic";

export async function generateMetadata() {
  const { t } = await getI18n();
  return { title: t.meta.themes };
}

const LEVEL_WIDTH: Record<HeatLevel, string> = { high: "100%", medium: "60%", low: "30%" };

type SearchParams = Record<string, string | string[] | undefined>;

/** First `?year=` value as an integer; null when absent or not numeric. */
function parseYearParam(params: SearchParams): number | null {
  const raw = params.year;
  const value = Array.isArray(raw) ? raw[0] : raw;
  const parsed = Number(value);
  return value && Number.isInteger(parsed) ? parsed : null;
}

/** `Q3` / `q3` / `3` → 3; anything else → null. */
function parseQuarterParam(params: SearchParams): Quarter | null {
  const raw = params.quarter;
  const value = (Array.isArray(raw) ? raw[0] : raw)?.toUpperCase() ?? null;
  if (!value) return null;
  const match = /^Q?([1-4])$/.exec(value);
  return match ? (Number(match[1]) as Quarter) : null;
}

/**
 * Resolve which periods to show. Only data-backed periods are selectable; an
 * unknown or absent quarter falls back to the most recent curated quarter.
 */
function resolvePeriods(params: SearchParams): {
  year: number;
  yearly: PeriodFindings | null;
  quarterly: { quarter: Quarter; entry: PeriodFindings | null } | null;
} {
  const years = getAvailableYears();
  const latestQuarter = getMostRecentQuarter();

  const year = parseYearParam(params) ?? years[0];
  const yearly = years.includes(year) ? getYearlyFindings(year) : null;

  const quarters = getAvailableQuarters(year);
  const requested = parseQuarterParam(params);
  const fallback = quarters[quarters.length - 1] ?? latestQuarter?.quarter ?? null;
  const fallbackYear = quarters.length > 0 ? year : latestQuarter?.year;
  const quarter = requested && quarters.includes(requested) ? requested : fallback;

  const quarterly =
    fallbackYear !== undefined && quarter
      ? { quarter, entry: getQuarterlyFindings(fallbackYear, quarter) }
      : null;

  return { year, yearly, quarterly };
}

/** A row of plain links over search params — no client JavaScript. */
function PeriodPicker({
  ariaLabel,
  options,
}: {
  ariaLabel: string;
  options: Array<{ href: string; label: string; hint: string | null; selected: boolean }>;
}) {
  return (
    <nav className="as-period-picker" aria-label={ariaLabel}>
      {options.map((option) => (
        <a
          key={option.href}
          href={option.href}
          aria-current={option.selected ? "true" : undefined}
          className={option.selected ? "as-period is-selected" : "as-period"}
        >
          {option.label}
          {option.hint ? <span className="as-meta"> · {option.hint}</span> : null}
        </a>
      ))}
    </nav>
  );
}

export default async function ThemesPage({
  searchParams,
}: {
  searchParams?: Promise<SearchParams>;
}) {
  const { locale, t } = await getI18n();
  const params = (await searchParams) ?? {};
  const { signals, companyCounts, fixture } = await getThemesData();
  const heat = computeThemeHeat(signals, companyCounts);
  const { year, yearly, quarterly } = resolvePeriods(params);

  const years = getAvailableYears();
  const quarters = getAvailableQuarters(year);
  const yearPicker = (
    <PeriodPicker
      ariaLabel={t.themes.findings.yearlyTitle}
      options={years.map((availableYear) => ({
        href: `/themes?year=${availableYear}`,
        label: String(availableYear),
        hint: getYearlyFindings(availableYear) ? t.themes.findings.yearToDate : null,
        selected: availableYear === year,
      }))}
    />
  );
  const quarterPicker =
    quarters.length > 0 ? (
      <PeriodPicker
        ariaLabel={t.themes.findings.quarterlyTitle}
        options={quarters.map((availableQuarter) => ({
          href: `/themes?year=${year}&quarter=Q${availableQuarter}`,
          label: t.themes.findings.quarterLabel(availableQuarter),
          hint: null,
          selected: quarterly?.quarter === availableQuarter,
        }))}
      />
    ) : null;

  return (
    <>
      <h1 className="as-page-title">{t.themes.title}</h1>
      <p className="as-page-sub">{t.themes.sub}</p>
      {fixture ? <FixtureNotice t={t} /> : null}

      <div className="as-heat-grid">
        {heat.map((entry) => (
          <section key={entry.theme.id} className="as-card as-heat-card">
            <h2 className="as-card-title">
              {locale === "zh" ? entry.theme.labelZh : entry.theme.labelEn}
            </h2>
            <div className="as-heat-bar" role="img" aria-label={t.themes.level[entry.level]}>
              <span style={{ width: LEVEL_WIDTH[entry.level] }} />
            </div>
            <p style={{ margin: "0.5rem 0 0.2rem" }}>
              <strong>{t.themes.signals(entry.signalCount)}</strong> ·{" "}
              <span className="as-meta">{t.themes.level[entry.level]}</span>
            </p>
            <p className="as-meta" style={{ margin: 0 }}>
              {t.themes.companies(entry.companyCount)}
            </p>
            <a className="as-theme-link" href={`/feed?theme=${encodeURIComponent(entry.theme.id)}`}>
              {t.themes.viewSignals}
            </a>
          </section>
        ))}
      </div>

      {fixture ? (
        <p className="as-meta" role="note" style={{ margin: "0 0 0.75rem" }}>
          {t.themes.findings.curated}
        </p>
      ) : null}

      <section className="as-findings" aria-labelledby="findings-yearly">
        <h2 id="findings-yearly" className="as-section-title">
          {t.themes.findings.yearlyTitle}
        </h2>
        {yearPicker}
        {yearly ? (
          <PeriodBlock period={yearly} locale={locale} t={t} />
        ) : (
          <p className="as-meta">{t.themes.findings.empty}</p>
        )}
      </section>

      <section className="as-findings" aria-labelledby="findings-quarterly">
        <h2 id="findings-quarterly" className="as-section-title">
          {t.themes.findings.quarterlyTitle}
        </h2>
        {quarterPicker}
        {quarterly?.entry ? (
          <PeriodBlock period={quarterly.entry} locale={locale} t={t} />
        ) : (
          <p className="as-meta">{t.themes.findings.empty}</p>
        )}
      </section>
    </>
  );
}

/** One period's narrative plus its sourced finding cards, in the request's language. */
function PeriodBlock({
  period,
  locale,
  t,
}: {
  period: PeriodFindings;
  locale: "en" | "zh";
  t: Messages;
}) {
  return (
    <article>
      <h3>{locale === "zh" ? period.titleZh : period.titleEn}</h3>
      <p>{locale === "zh" ? period.summaryZh : period.summaryEn}</p>
      <div className="as-heat-grid">
        {period.findings.map((finding, index) => (
          <article key={`${finding.themeId}-${index}`} className="as-card">
            <p className="as-meta" style={{ margin: 0 }}>
              {themeLabel(finding.themeId, locale)}
            </p>
            <h4 className="as-card-title">{locale === "zh" ? finding.headlineZh : finding.headlineEn}</h4>
            <p>{locale === "zh" ? finding.detailZh : finding.detailEn}</p>
            <p className="as-meta" style={{ margin: 0 }}>
              {t.themes.findings.sourceHeading}:{" "}
              <a href={finding.sourceUrl} target="_blank" rel="noopener noreferrer">
                {finding.sourceLabel}
              </a>
            </p>
          </article>
        ))}
      </div>
    </article>
  );
}
