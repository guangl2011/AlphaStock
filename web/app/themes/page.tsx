import { FixtureNotice } from "@/components/fixture-notice";
import { getThemesData } from "@/lib/data";
import { computeThemeHeat, type HeatLevel } from "@/lib/themes";

export const dynamic = "force-dynamic";

export const metadata = { title: "Themes · AlphaStock" };

const LEVEL_WIDTH: Record<HeatLevel, string> = {
  high: "100%",
  medium: "60%",
  low: "30%",
};

const LEVEL_LABEL: Record<HeatLevel, string> = {
  high: "high heat",
  medium: "medium heat",
  low: "low heat",
};

export default async function ThemesPage() {
  const { signals, companyCounts, fixture } = await getThemesData();
  const heat = computeThemeHeat(signals, companyCounts);

  return (
    <>
      <h1 className="as-page-title">Theme Explorer · 主题热度</h1>
      <p className="as-page-sub">
        Signal heat across the seven coverage themes — signal count and strength over the
        trailing 90 days, with recency decay. It is a research-density indicator, not a
        performance metric.{" "}
        <span lang="zh-Hans">
          七大主题的信号热度：衡量近 90 天的信号数量与强度（含时间衰减），衡量研究密度而非业绩。
        </span>
      </p>
      {fixture ? <FixtureNotice /> : null}

      <div className="as-heat-grid">
        {heat.map((entry) => (
          <section key={entry.theme.id} className="as-card as-heat-card">
            <h2 className="as-card-title">
              {entry.theme.labelEn} · <span lang="zh-Hans">{entry.theme.labelZh}</span>
            </h2>
            <div className="as-heat-bar" role="img" aria-label={LEVEL_LABEL[entry.level]}>
              <span style={{ width: LEVEL_WIDTH[entry.level] }} />
            </div>
            <p style={{ margin: "0.5rem 0 0.2rem" }}>
              <strong>{entry.signalCount}</strong> signal
              {entry.signalCount === 1 ? "" : "s"} ·{" "}
              <span className="as-meta">{LEVEL_LABEL[entry.level]}</span>
            </p>
            <p className="as-meta" style={{ margin: 0 }}>
              {entry.companyCount} covered compan
              {entry.companyCount === 1 ? "y" : "ies"}
            </p>
            <a
              className="as-theme-link"
              href={`/feed?theme=${encodeURIComponent(entry.theme.id)}`}
            >
              View signals · 查看信号 →
            </a>
          </section>
        ))}
      </div>
    </>
  );
}
