"""Relationship model: index and stock returns vs factors, plus the
V1–V4 signal-event study.

Three layers, all pure functions of aligned daily-return series:

* **Index ↔ factors** — Pearson correlations (full sample and trailing
  63/21-day windows) and a closed-form OLS exposure regression on
  ``REGRESSION_FACTORS``.
* **Stock ↔ factors** — trailing 63-day beta and correlation vs ``MKT``
  and each thematic factor, same math as the index layer.
* **Signal events** — abnormal return over ``[t-1, t+3]``: the stock's
  compounded return from the close at ``t-1`` to the close at ``t+3``
  (returns ``r_t .. r_t+3``) minus its pre-event beta times the market's
  compounded return over the same window. The beta is estimated from the
  ``ESTIMATION_WINDOW`` returns immediately before ``t`` (``r_t-63 ..
  r_t-1``) — never from data on or after the event date (no lookahead).

All windows are trailing only; undefined statistics are ``None`` and
serialize as empty, never as 0.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from ..scoring.alpha_score import SignalEvent
from .factors import REGRESSION_FACTORS
from .stats import StatsError, ols_solve, pearson

#: Trailing windows, in trading days, per the spec.
WINDOW_LONG = 63
WINDOW_SHORT = 21

#: Returns immediately before the event window used to estimate the beta
#: (r_t-63 .. r_t-1 — never data on or after the event date).
ESTIMATION_WINDOW = 63

#: Event window: compounded return from the close at t-1 to the close at
#: t+3 — the returns r_t .. r_t+3, four trailing returns.
EVENT_AFTER = 3


class ModelError(ValueError):
    """Raised when the model receives inconsistent inputs."""


@dataclass(frozen=True)
class SeriesView:
    """A daily-return series plus its sorted dates, aligned once."""

    name: str
    returns: dict[date, float]

    @property
    def dates(self) -> list[date]:
        return sorted(self.returns)

    def aligned(self, other: SeriesView) -> tuple[list[float], list[float]]:
        """Return pairs on the common dates of both series."""
        common = sorted(set(self.returns) & set(other.returns))
        return (
            [self.returns[d] for d in common],
            [other.returns[d] for d in common],
        )


def index_factor_exposures(
    index_id: str,
    index_returns: Mapping[date, float],
    factors: Mapping[str, Mapping[date, float]],
) -> list[dict[str, object]]:
    """Per-factor exposure rows for one index: OLS (on REGRESSION_FACTORS)
    plus Pearson correlations over the full sample and both trailing windows.

    One OLS fit is run per index; the same collinearity report applies to
    every factor row. Rows are sorted by factor name.
    """
    index_view = SeriesView(index_id, dict(index_returns))
    regression = _aligned_regression(index_view, factors, list(REGRESSION_FACTORS))
    if regression is None:
        raise ModelError(
            f"{index_id}: fewer than two days align between index returns and factors"
        )
    result, regress_dates = regression

    rows: list[dict[str, object]] = []
    for factor in sorted(factors):
        xs, ys = SeriesView(factor, dict(factors[factor])).aligned(index_view)
        if len(xs) < 2:
            raise ModelError(
                f"{index_id}: factor {factor} has {len(xs)} aligned return days"
            )
        rows.append(
            {
                "index_id": index_id,
                "factor": factor,
                "beta": result.betas.get(factor),
                "std_error": result.std_errors.get(factor),
                "t_stat": result.t_stats.get(factor),
                "r_squared": result.r_squared,
                "corr_full": pearson(xs, ys),
                "corr_63d": pearson(xs[-WINDOW_LONG:], ys[-WINDOW_LONG:])
                if len(xs) >= WINDOW_LONG
                else None,
                "corr_21d": pearson(xs[-WINDOW_SHORT:], ys[-WINDOW_SHORT:])
                if len(xs) >= WINDOW_SHORT
                else None,
                "dropped_collinear": result.dropped_collinear,
                "regression_days": len(regress_dates),
            }
        )
    return rows


def _aligned_regression(
    view: SeriesView, factors: Mapping[str, Mapping[date, float]], names: list[str]
) -> tuple[object, list[date]] | None:
    """OLS of the view's returns on ``names`` over their common dates.

    Returns ``(OLSResult, aligned_dates)`` or ``None`` when fewer than two
    days align.
    """
    common: set[date] | None = None
    for name in names:
        dates = set(factors[name])
        common = dates if common is None else (common & dates)
    if common is None:
        common = set()
    common &= set(view.returns)
    dates = sorted(common)
    if len(dates) < 2:
        return None
    try:
        result = ols_solve(
            [view.returns[d] for d in dates],
            {name: [factors[name][d] for d in dates] for name in names},
        )
    except StatsError as exc:
        raise ModelError(f"{view.name}: OLS failed: {exc}") from exc
    return result, dates


@dataclass(frozen=True)
class StockFactorRow:
    """Trailing 63-day single-factor exposure for one stock."""

    ticker: str
    factor: str
    beta: float | None
    corr: float | None


def stock_factor_rows(
    ticker: str,
    stock_returns: Mapping[date, float],
    factors: Mapping[str, Mapping[date, float]],
    factor_names: Sequence[str],
    window: int = WINDOW_LONG,
) -> list[StockFactorRow]:
    """Trailing-window beta and correlation of one stock vs each factor.

    Beta comes from a single-factor OLS with intercept over the last
    ``window`` aligned returns; a window with too few days or zero factor
    variance yields ``None`` — reported empty, never as 0.
    """
    stock_view = SeriesView(ticker, dict(stock_returns))
    rows: list[StockFactorRow] = []
    for factor in sorted(factor_names):
        xs, ys = SeriesView(factor, dict(factors[factor])).aligned(stock_view)
        tail_x, tail_y = xs[-window:], ys[-window:]
        beta: float | None = None
        if len(tail_x) >= 3 and len(set(tail_x)) > 1:
            try:
                fit = ols_solve(tail_y, {factor: tail_x})
                beta = fit.betas[factor]
            except StatsError:
                beta = None
        rows.append(
            StockFactorRow(
                ticker=ticker,
                factor=factor,
                beta=beta,
                corr=pearson(tail_x, tail_y) if len(tail_x) >= window else None,
            )
        )
    return rows


@dataclass(frozen=True)
class EventAbnormalReturn:
    """One signal event's beta-adjusted abnormal return."""

    signal_id: str
    ticker: str
    category: str
    direction: str
    event_date: date
    beta: float
    stock_cum_return: float
    market_cum_return: float
    abnormal_return: float


@dataclass
class EventAggregates:
    """Mean abnormal return by direction and by category."""

    by_direction: dict[str, dict[str, float | int]] = field(default_factory=dict)
    by_category: dict[str, dict[str, float | int]] = field(default_factory=dict)


def event_abnormal_returns(
    signals: Sequence[SignalEvent],
    stock_returns: Mapping[str, Mapping[date, float]],
    mkt_returns: Mapping[date, float],
    estimation_window: int = ESTIMATION_WINDOW,
) -> list[EventAbnormalReturn]:
    """Abnormal return per (signal, covered ticker) pair.

    ``t`` is the signal's ``published_at`` date and must be a trading day
    present in both the stock's and the market's return series — a missing
    date or estimation window raises :class:`ModelError` loudly (never a
    silent skip). Events for tickers with no return series raise as well.
    """
    days = sorted(mkt_returns)
    position = {d: i for i, d in enumerate(days)}
    out: list[EventAbnormalReturn] = []
    for signal in signals:
        if not signal.tickers:
            continue
        event_day = signal.published_at.date()
        if event_day not in position:
            raise ModelError(
                f"signal {signal.signal_id}: event date {event_day} is not a "
                "trading day in the price data"
            )
        i = position[event_day]
        # r_t .. r_t+3 is the return span of the [t-1, t+3] close window.
        # Guards are loud: negative indices would silently wrap, and a
        # window past the data end would silently truncate.
        if i - estimation_window < 0:
            raise ModelError(
                f"signal {signal.signal_id}: not enough estimation history before "
                f"{event_day} (needs {estimation_window} returns, have {i})"
            )
        if i + EVENT_AFTER >= len(days):
            raise ModelError(
                f"signal {signal.signal_id}: event window [t-1, t+3] for "
                f"{event_day} runs past the last trading day in the data"
            )
        est_positions = list(range(i - estimation_window, i))
        window_positions = list(range(i, i + EVENT_AFTER + 1))
        est_days = [days[p] for p in est_positions]
        window_days = [days[p] for p in window_positions]
        market_est = [mkt_returns[d] for d in est_days]
        market_window = [mkt_returns[d] for d in window_days]

        for ticker in sorted(signal.tickers):
            series = stock_returns.get(ticker)
            if not series:
                raise ModelError(
                    f"signal {signal.signal_id}: ticker {ticker} has no return series"
                )
            missing = [d for d in [*est_days, *window_days] if d not in series]
            if missing:
                raise ModelError(
                    f"signal {signal.signal_id}: {ticker} is missing returns on "
                    f"{missing[0].isoformat()} (and {len(missing) - 1} more)"
                )
            stock_est = [series[d] for d in est_days]
            stock_window = [series[d] for d in window_days]
            fit = ols_solve(stock_est, {"MKT": market_est})
            beta = fit.betas["MKT"]
            stock_cum = _compound(stock_window)
            market_cum = _compound(market_window)
            out.append(
                EventAbnormalReturn(
                    signal_id=signal.signal_id,
                    ticker=ticker,
                    category=signal.category,
                    direction=signal.direction,
                    event_date=event_day,
                    beta=beta,
                    stock_cum_return=stock_cum,
                    market_cum_return=market_cum,
                    abnormal_return=stock_cum - beta * market_cum,
                )
            )
    return out


def _check_range(signal_id: str, first_position: int, label: str, length: int) -> None:
    if first_position < 0 or first_position >= length:
        raise ModelError(
            f"signal {signal_id}: not enough price history {label} "
            f"(needs position {first_position}, have {length} days)"
        )


def _compound(returns: Sequence[float]) -> float:
    compounded = 1.0
    for r in returns:
        compounded *= 1.0 + r
    return compounded - 1.0


def aggregate_ars(ars: Sequence[EventAbnormalReturn]) -> EventAggregates:
    """Mean abnormal return by direction and by category (mean of the raw ARs)."""
    by_direction: dict[str, list[float]] = {}
    by_category: dict[str, list[float]] = {}
    for event in ars:
        by_direction.setdefault(event.direction, []).append(event.abnormal_return)
        by_category.setdefault(event.category, []).append(event.abnormal_return)
    return EventAggregates(
        by_direction={
            k: {"count": len(v), "mean_abnormal_return": sum(v) / len(v)}
            for k, v in sorted(by_direction.items())
        },
        by_category={
            k: {"count": len(v), "mean_abnormal_return": sum(v) / len(v)}
            for k, v in sorted(by_category.items())
        },
    )
