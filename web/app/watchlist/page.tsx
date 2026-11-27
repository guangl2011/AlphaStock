import Link from "next/link";

import { DirectionBadge, StrengthBadge } from "@/components/badges";
import { EmptyState } from "@/components/empty-state";
import { FixtureNotice } from "@/components/fixture-notice";
import { AddTickerForm } from "@/components/watchlist-forms";
import { getAlerts, getCompanies, getSignals } from "@/lib/data";
import { formatDelta } from "@/lib/format";
import { getI18n } from "@/lib/i18n";
import {
  alertChannelLabel,
  buildWatchlistRows,
  currentTier,
  preferredSetting,
  settingsForTicker,
  watchlistLimit,
} from "@/lib/watchlist";
import { activeWatchlistStore } from "@/lib/watchlist-store";
import { removeTickerAction, updateAlertSettingAction } from "./actions";

export const dynamic = "force-dynamic";

export async function generateMetadata() {
  const { t } = await getI18n();
  return { title: t.meta.watchlist };
}

/**
 * The §7.2 Watchlist — the signed-in user's saved tickers, enriched with the
 * same row pattern as the Screener (latest signal badges, Alpha Score + weekly
 * delta). User rows flow only through the per-user store: RLS scopes them to
 * auth.uid() in supabase mode, and fixture mode keeps everything in memory.
 */
export default async function WatchlistPage() {
  const { t } = await getI18n();
  const [{ companies }, { signals }, { alerts }, watchlist] = await Promise.all([
    getCompanies(),
    getSignals({}),
    getAlerts(),
    activeWatchlistStore().then((store) => store.load()),
  ]);

  const tier = currentTier();
  const limit = watchlistLimit(tier);
  const atLimit = limit !== null && watchlist.tickers.length >= limit;
  const rows = buildWatchlistRows(watchlist.tickers, companies, signals, alerts);

  const addLabels = {
    addLabel: t.watchlist.addLabel,
    placeholder: t.watchlist.addPlaceholder,
    submit: t.watchlist.add,
    submitting: t.watchlist.adding,
    invalid: t.watchlist.invalid,
    duplicate: t.watchlist.duplicate,
    limit: t.watchlist.limitReached(limit ?? 0),
    service: t.watchlist.service,
  };

  return (
    <>
      <h1 className="as-page-title">{t.watchlist.title}</h1>
      <p className="as-page-sub">{t.watchlist.sub}</p>
      <FixtureNotice t={t} />

      {atLimit ? (
        <div className="as-empty" role="status" data-testid="watchlist-at-limit">
          <p style={{ fontWeight: 600, margin: "0 0 0.25rem" }}>
            {t.watchlist.limitReached(limit)}
          </p>
          <p style={{ margin: 0 }}>{t.watchlist.limitHint}</p>
        </div>
      ) : (
        <AddTickerForm labels={addLabels} />
      )}
      <p className="as-page-sub" data-testid="watchlist-usage">
        {t.watchlist.usage(watchlist.tickers.length, limit)}
      </p>

      {rows.length === 0 ? (
        <EmptyState title={t.watchlist.emptyTitle} hint={t.watchlist.emptyHint} />
      ) : (
        <div className="as-table-wrap">
          <table className="as-table">
            <thead>
              <tr>
                <th scope="col">{t.watchlist.colTicker}</th>
                <th scope="col">{t.watchlist.colCompany}</th>
                <th scope="col">{t.watchlist.colLatestSignal}</th>
                <th scope="col">{t.watchlist.colScore}</th>
                <th scope="col">{t.watchlist.colAlerts}</th>
                <th scope="col">
                  <span className="visually-hidden">{t.watchlist.remove}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const preferred = preferredSetting(watchlist.settings, row.ticker);
                const stored = settingsForTicker(watchlist.settings, row.ticker);
                return (
                  <tr key={row.ticker} data-ticker={row.ticker}>
                    <td>
                      <Link href={`/companies/${row.ticker}`} className="as-alert-ticker">
                        {row.ticker}
                      </Link>
                    </td>
                    <td>{row.name}</td>
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
                      <form
                        action={updateAlertSettingAction}
                        className="as-watchlist-alerts"
                        aria-label={`${row.ticker} ${t.watchlist.colAlerts}`}
                      >
                        <input type="hidden" name="ticker" value={row.ticker} />
                        {stored.map((setting) => (
                          <span
                            key={setting.channel}
                            className={`as-badge ${setting.enabled ? "as-badge-positive" : "as-badge-neutral"}`}
                          >
                            {alertChannelLabel(setting.channel, t.watchlist)}
                            {": "}
                            {setting.enabled ? "✓" : "✗"}
                          </span>
                        ))}
                        <label>
                          {t.watchlist.alertChannel}
                          <select name="channel" defaultValue={preferred.channel}>
                            <option value="in_app">{t.watchlist.channelInApp}</option>
                            <option value="email">{t.watchlist.channelEmail}</option>
                          </select>
                        </label>
                        <label>
                          <input type="checkbox" name="enabled" defaultChecked={preferred.enabled} />
                          {t.watchlist.alertEnabled}
                        </label>
                        <button type="submit">{t.watchlist.alertSave}</button>
                      </form>
                    </td>
                    <td>
                      <form action={removeTickerAction}>
                        <input type="hidden" name="ticker" value={row.ticker} />
                        <button type="submit" aria-label={`${t.watchlist.remove} ${row.ticker}`}>
                          {t.watchlist.remove}
                        </button>
                      </form>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
