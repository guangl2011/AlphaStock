import { SignalCard } from "@/components/signal-card";
import { heatPercent, isFromToday, scoreTilt } from "@/lib/daily-alpha";
import type { DailyAlphaBrief } from "@/lib/daily-alpha";
import type { Locale } from "@/lib/i18n";
import type { Messages } from "@/lib/messages";

/**
 * The four-section Daily Alpha digest (§7.2): takeaway / key signals / theme
 * heat / macro sentence. Pure and presentational — `now` is injected so the
 * fallback behavior is deterministic in tests. Free-tier display rules apply:
 * scenario ranges never render (SignalCard structurally drops them) and Alpha
 * Score annotations render as a qualitative tilt, never a number.
 */
export function DailyAlphaDigest({
  brief,
  t,
  locale,
  now,
}: {
  brief: DailyAlphaBrief | null;
  t: Messages;
  locale: Locale;
  now: Date;
}) {
  if (!brief) {
    return <p className="as-page-sub">{t.dailyAlpha.emptyNotice}</p>;
  }
  const title = locale === "zh" ? brief.titleZh : brief.titleEn;
  const takeaway = locale === "zh" ? brief.takeawayZh : brief.takeawayEn;
  const macro = locale === "zh" ? brief.macroZh : brief.macroEn;
  const maxHeat = Math.max(...brief.themeHeat.map((row) => row.count), 0);
  const tickers = [...new Set(brief.items.flatMap((item) => item.tickers))]
    .sort()
    .filter((ticker) => brief.scores[ticker]);

  return (
    <>
      <h1 className="as-page-title">{title}</h1>
      {!isFromToday(brief.asOf, now) ? (
        <p className="as-meta" role="note" style={{ margin: "0.2rem 0 0" }}>
          {t.dailyAlpha.fallbackNotice(brief.asOf.slice(0, 10))}
        </p>
      ) : null}

      <section aria-labelledby="daily-alpha-takeaway">
        <h2 id="daily-alpha-takeaway" className="as-section-title">
          {t.dailyAlpha.takeawayTitle}
        </h2>
        <p>{takeaway}</p>
      </section>

      <section aria-labelledby="daily-alpha-signals">
        <h2 id="daily-alpha-signals" className="as-section-title">
          {t.dailyAlpha.signalsTitle}
        </h2>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem", margin: "0.3rem 0" }}>
          {tickers.map((ticker) => {
            const tilt = scoreTilt(brief.scores[ticker].delta);
            return (
              <span key={ticker} className="as-platform-chip">
                {ticker} · {t.dailyAlpha.scoreTilt[tilt]}
              </span>
            );
          })}
        </div>
        {brief.items.map((item) => (
          <SignalCard key={item.signalId} signal={item} t={t} locale={locale} />
        ))}
      </section>

      <section aria-labelledby="daily-alpha-heat">
        <h2 id="daily-alpha-heat" className="as-section-title">
          {t.dailyAlpha.heatTitle}
        </h2>
        <ul style={{ listStyle: "none", padding: 0, margin: "0.3rem 0" }}>
          {brief.themeHeat.map((row) => (
            <li key={row.theme} style={{ margin: "0.35rem 0" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: "0.75rem" }}>
                <span>{row.theme}</span>
                <span className="as-meta">{t.dailyAlpha.signalsInTheme(row.count)}</span>
              </div>
              <div
                className="as-heat-bar"
                role="img"
                aria-label={`${row.theme}: ${t.dailyAlpha.signalsInTheme(row.count)}`}
              >
                <span style={{ width: `${heatPercent(row.count, maxHeat)}%`, marginLeft: 0 }} />
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="daily-alpha-macro">
        <h2 id="daily-alpha-macro" className="as-section-title">
          {t.dailyAlpha.macroTitle}
        </h2>
        <p>{macro}</p>
      </section>
    </>
  );
}
