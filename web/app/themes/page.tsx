import { FixtureNotice } from "@/components/fixture-notice";
import { getThemesData } from "@/lib/data";
import { getI18n } from "@/lib/i18n";
import { computeThemeHeat, type HeatLevel } from "@/lib/themes";

export const dynamic = "force-dynamic";

export async function generateMetadata() {
  const { t } = await getI18n();
  return { title: t.meta.themes };
}

const LEVEL_WIDTH: Record<HeatLevel, string> = { high: "100%", medium: "60%", low: "30%" };

export default async function ThemesPage() {
  const { locale, t } = await getI18n();
  const { signals, companyCounts, fixture } = await getThemesData();
  const heat = computeThemeHeat(signals, companyCounts);

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
    </>
  );
}
