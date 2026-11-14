import { EmptyState } from "@/components/empty-state";
import { FixtureNotice } from "@/components/fixture-notice";
import { SignalCard } from "@/components/signal-card";
import { getSignals } from "@/lib/data";
import { CATEGORIES } from "@/lib/categories";
import { hasActiveFilters, parseSignalFilters } from "@/lib/filters";
import { getI18n } from "@/lib/i18n";
import { THEMES } from "@/lib/themes";

export const dynamic = "force-dynamic";

export async function generateMetadata() {
  const { t } = await getI18n();
  return { title: t.meta.feed };
}

const STRENGTH_OPTIONS = [1, 2, 3, 4, 5] as const;

export default async function FeedPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { locale, t } = await getI18n();
  const filters = parseSignalFilters(await searchParams);
  const { signals, fixture } = await getSignals(filters);

  return (
    <>
      <h1 className="as-page-title">{t.feed.title}</h1>
      <p className="as-page-sub">{t.feed.sub}</p>
      {fixture ? <FixtureNotice t={t} /> : null}

      <form className="as-filters" method="get" action="/feed">
        <label>
          {t.feed.theme}
          <select name="theme" defaultValue={filters.theme ?? ""}>
            <option value="">{t.feed.allThemes}</option>
            {THEMES.map((theme) => (
              <option key={theme.id} value={theme.id}>
                {locale === "zh" ? theme.labelZh : theme.labelEn}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t.feed.category}
          <select name="category" defaultValue={filters.category ?? ""}>
            <option value="">{t.feed.allCategories}</option>
            {CATEGORIES.map((category) => (
              <option key={category.id} value={category.id}>
                {locale === "zh" ? category.labelZh : category.labelEn}
                {category.proprietary ? " ★" : ""}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t.feed.minStrength}
          <select name="strength" defaultValue={filters.minStrength ?? ""}>
            <option value="">{t.feed.any}</option>
            {STRENGTH_OPTIONS.map((strength) => (
              <option key={strength} value={strength}>
                {strength}+
              </option>
            ))}
          </select>
        </label>
        <button type="submit">{t.feed.apply}</button>
        {hasActiveFilters(filters) ? <a href="/feed">{t.feed.clear}</a> : null}
      </form>

      {signals.length === 0 ? (
        <EmptyState title={t.feed.emptyTitle} hint={t.feed.emptyHint} />
      ) : (
        signals.map((signal) => (
          <SignalCard key={signal.signalId} signal={signal} t={t} locale={locale} />
        ))
      )}
    </>
  );
}
