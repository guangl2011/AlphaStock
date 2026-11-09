import { EmptyState } from "@/components/empty-state";
import { FixtureNotice } from "@/components/fixture-notice";
import { SignalCard } from "@/components/signal-card";
import { getSignals } from "@/lib/data";
import { CATEGORIES } from "@/lib/categories";
import { hasActiveFilters, parseSignalFilters } from "@/lib/filters";
import { THEMES } from "@/lib/themes";

export const dynamic = "force-dynamic";

export const metadata = { title: "Signal Feed · AlphaStock" };

const STRENGTH_OPTIONS = [1, 2, 3, 4, 5] as const;

export default async function FeedPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const filters = parseSignalFilters(await searchParams);
  const { signals, fixture } = await getSignals(filters);

  return (
    <>
      <h1 className="as-page-title">Signal Feed · 信号流</h1>
      <p className="as-page-sub">
        Verified, source-linked signals across AI energy and Physical AI.{" "}
        <span lang="zh-Hans">经核验、附来源的信号流。</span>
      </p>
      {fixture ? <FixtureNotice /> : null}

      <form className="as-filters" method="get" action="/feed">
        <label>
          Theme · 主题
          <select name="theme" defaultValue={filters.theme ?? ""}>
            <option value="">All themes</option>
            {THEMES.map((theme) => (
              <option key={theme.id} value={theme.id}>
                {theme.labelEn} · {theme.labelZh}
              </option>
            ))}
          </select>
        </label>
        <label>
          Category · 类别
          <select name="category" defaultValue={filters.category ?? ""}>
            <option value="">All categories</option>
            {CATEGORIES.map((category) => (
              <option key={category.id} value={category.id}>
                {category.labelEn}
                {category.proprietary ? " ★" : ""}
              </option>
            ))}
          </select>
        </label>
        <label>
          Min strength · 最低强度
          <select name="strength" defaultValue={filters.minStrength ?? ""}>
            <option value="">Any</option>
            {STRENGTH_OPTIONS.map((strength) => (
              <option key={strength} value={strength}>
                {strength}+
              </option>
            ))}
          </select>
        </label>
        <button type="submit">Apply</button>
        {hasActiveFilters(filters) ? <a href="/feed">Clear</a> : null}
      </form>

      {signals.length === 0 ? (
        <EmptyState
          title="No signals match these filters"
          hint="Try widening the filters — or clear them — to see the full stream."
        />
      ) : (
        signals.map((signal) => <SignalCard key={signal.signalId} signal={signal} />)
      )}
    </>
  );
}
