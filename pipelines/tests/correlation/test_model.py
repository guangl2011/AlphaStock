"""Model tests: index exposures, stock rows, and the event study.

The event-study case is fully hand-computed: a stock whose returns are
exactly 1.5x the market through estimation, then a known window.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from pipelines.correlation.model import (
    ESTIMATION_WINDOW,
    EVENT_AFTER,
    ModelError,
    aggregate_ars,
    event_abnormal_returns,
    index_factor_exposures,
    stock_factor_rows,
)
from pipelines.correlation.stats import pearson
from pipelines.scoring.alpha_score import SignalEvent


def signal(
    signal_id: str,
    tickers: tuple[str, ...],
    day: date,
    category: str = "V2_interconnection",
    direction: str = "positive",
) -> SignalEvent:
    return SignalEvent(
        signal_id=signal_id,
        category=category,
        direction=direction,
        strength=4,
        published_at=datetime(day.year, day.month, day.day, 20, 0, tzinfo=UTC),
        tickers=tickers,
    )


def full_factor_set(days: list[date]) -> dict[str, dict[date, float]]:
    """All eight factors with mutually independent return series — the
    regression runs on MKT + BREADTH + the four thematic factors, so all
    must be present and none may be an affine copy of another."""
    mkt = [0.01, -0.004, 0.012, 0.002, -0.009, 0.006, 0.015, -0.002, 0.007, 0.001]
    breadth = [0.003, 0.008, -0.005, 0.011, 0.001, -0.007, 0.004, 0.009, -0.003, 0.002]
    grid = [0.02, -0.01, 0.004, 0.013, -0.006, 0.0, 0.011, -0.008, 0.005, 0.009]
    util = [0.002, 0.005, -0.003, 0.006, 0.0, -0.004, 0.001, 0.007, -0.002, 0.003]
    nuke = [0.012, -0.006, 0.008, -0.002, 0.014, 0.003, -0.005, 0.009, 0.001, -0.008]
    robo = [0.006, 0.004, -0.001, 0.009, -0.012, 0.002, 0.008, -0.004, 0.003, 0.005]
    return {
        "MKT": {d: v for d, v in zip(days, mkt, strict=False)},
        "BREADTH": {d: v for d, v in zip(days, breadth, strict=False)},
        "GRID": {d: v for d, v in zip(days, grid, strict=False)},
        "UTIL": {d: v for d, v in zip(days, util, strict=False)},
        "NUKE": {d: v for d, v in zip(days, nuke, strict=False)},
        "ROBO": {d: v for d, v in zip(days, robo, strict=False)},
        # SIZE/MICRO are correlation-only in the model, so scaling MKT is fine.
        "SIZE": {d: 0.5 * v for d, v in zip(days, mkt, strict=False)},
        "MICRO": {d: 0.3 * v for d, v in zip(days, mkt, strict=False)},
    }


def test_index_exposure_recovers_exact_linear_index():
    # Synthetic index: r = 0.7*MKT + 0.3*BREADTH + 0.1*GRID, no noise.
    mkt = [0.01, -0.004, 0.012, 0.002, -0.009, 0.006, 0.015, -0.002, 0.007, 0.001]
    breadth = [0.003, 0.008, -0.005, 0.011, 0.001, -0.007, 0.004, 0.009, -0.003, 0.002]
    grid = [0.02, -0.01, 0.004, 0.013, -0.006, 0.0, 0.011, -0.008, 0.005, 0.009]
    index = [0.7 * m + 0.3 * b + 0.1 * g for m, b, g in zip(mkt, breadth, grid, strict=False)]
    start = date(2026, 1, 5)
    days = [start + timedelta(days=i) for i in range(len(mkt))]
    factors = full_factor_set(days)
    # A factor exactly duplicating GRID must be dropped and reported.
    factors["ROBO"] = dict(factors["GRID"])
    rows = index_factor_exposures("AS-100", dict(zip(days, index, strict=False)), factors)

    by_factor = {row["factor"]: row for row in rows}
    assert by_factor["MKT"]["beta"] == pytest.approx(0.7, abs=1e-9)
    assert by_factor["BREADTH"]["beta"] == pytest.approx(0.3, abs=1e-9)
    assert by_factor["GRID"]["beta"] == pytest.approx(0.1, abs=1e-9)
    assert by_factor["MKT"]["r_squared"] == pytest.approx(1.0, abs=1e-9)
    assert by_factor["MKT"]["dropped_collinear"] == ("ROBO",)
    # Correlation-only factors are regression-empty, never zero; their
    # correlation is still computed and must match direct Pearson.
    micro_series = list(factors["MICRO"].values())
    assert by_factor["MICRO"]["beta"] is None
    assert by_factor["MICRO"]["corr_full"] == pytest.approx(
        pearson(micro_series, index), abs=1e-12
    )


def test_index_exposure_needs_enough_aligned_days():
    start = date(2026, 1, 5)
    factors = full_factor_set([start + timedelta(days=i) for i in range(10)])
    # Index has only one return day: no regression or correlation is defined.
    with pytest.raises(ModelError, match="fewer than two days"):
        index_factor_exposures(
            "AS-100", {start + timedelta(days=1): 0.01}, factors
        )


def test_stock_factor_rows_trailing_beta_and_corr():
    # 70 days: stock = 0.8*mkt + noise-free drift; the trailing 63-day beta
    # must be exactly 0.8 regardless of the first week.
    start = date(2026, 1, 5)
    days = [start + timedelta(days=i) for i in range(70)]
    mkt = [0.01 if i % 2 == 0 else -0.006 for i in range(70)]
    stock = [0.8 * m for m in mkt]
    factors = {"MKT": dict(zip(days, mkt, strict=False))}
    rows = stock_factor_rows("TEST", dict(zip(days, stock, strict=False)), factors, ["MKT"])
    assert rows[0].beta == pytest.approx(0.8, abs=1e-9)
    assert rows[0].corr == pytest.approx(1.0, abs=1e-9)


def test_stock_factor_rows_too_short_window_is_empty_not_zero():
    days = [date(2026, 1, 5) + timedelta(days=i) for i in range(10)]
    mkt = [0.01 * (1 if i % 2 else -1) for i in range(10)]
    rows = stock_factor_rows(
        "TEST",
        dict(zip(days, mkt, strict=False)),
        {"MKT": dict(zip(days, mkt, strict=False))},
        ["MKT"],
    )
    # Fewer than 63 aligned days: corr is empty, beta still computable.
    assert rows[0].corr is None
    assert rows[0].beta == pytest.approx(1.0, abs=1e-9)


def test_event_study_hand_computed_abnormal_return():
    # Estimation: stock = 1.5 * MKT exactly (64 returns with variance).
    # Window [t-1, t+3] = returns r_t .. r_t+3 (4 returns, positions i..i+3):
    #   MKT:   0.01 x4                       -> cum = 1.01^4 - 1 = 0.04060401
    #   stock: 0.02, 0.00, 0.01, 0.01        -> cum = 1.02*1.01*1.01 - 1 = 0.040502
    #   AR = 0.040502 - 1.5 * 0.04060401 = -0.020404015
    n = ESTIMATION_WINDOW + 6
    start = date(2026, 1, 5)
    days = [start + timedelta(days=i) for i in range(n)]
    i = ESTIMATION_WINDOW + 1
    mkt = {d: (0.01 if k % 2 == 0 else -0.005) for k, d in enumerate(days)}
    for d in days[i:]:
        mkt[d] = 0.01
    stock = {d: 1.5 * r for d, r in mkt.items()}
    for d, r in zip(days[i:], [0.02, 0.00, 0.01, 0.01, 0.01], strict=False):
        stock[d] = r

    events = event_abnormal_returns(
        [signal("SIG-1", ("TEST",), days[i])], {"TEST": stock}, mkt
    )
    assert len(events) == 1
    event = events[0]
    assert event.beta == pytest.approx(1.5, abs=1e-9)
    assert event.market_cum_return == pytest.approx(0.04060401, abs=1e-12)
    assert event.stock_cum_return == pytest.approx(0.040502, abs=1e-12)
    assert event.abnormal_return == pytest.approx(-0.020404015, abs=1e-9)


def test_event_study_direction_aggregation():
    n = ESTIMATION_WINDOW + 6
    start = date(2026, 1, 5)
    days = [start + timedelta(days=i) for i in range(n)]
    mkt = {d: 0.01 if k % 2 == 0 else -0.005 for k, d in enumerate(days)}
    stock = {d: 1.5 * r for d, r in mkt.items()}
    events = event_abnormal_returns(
        [
            signal("SIG-POS", ("TEST",), days[ESTIMATION_WINDOW + 1], direction="positive"),
            signal(
                "SIG-NEG",
                ("TEST",),
                days[ESTIMATION_WINDOW + 2],
                direction="negative",
                category="V1_news",
            ),
        ],
        {"TEST": stock},
        mkt,
    )
    aggregates = aggregate_ars(events)
    assert set(aggregates.by_direction) == {"negative", "positive"}
    assert aggregates.by_direction["positive"]["count"] == 1
    assert aggregates.by_direction["negative"]["count"] == 1
    assert set(aggregates.by_category) == {"V1_news", "V2_interconnection"}


def test_event_on_non_trading_day_fails_loudly():
    mkt = {date(2026, 1, 5) + timedelta(days=i): 0.01 for i in range(80)}
    stock = dict(mkt)
    with pytest.raises(ModelError, match="not a trading day"):
        event_abnormal_returns(
            [signal("SIG-X", ("TEST",), date(2026, 6, 1))], {"TEST": stock}, mkt
        )


def test_event_window_past_data_end_fails_loudly():
    n = ESTIMATION_WINDOW + 2  # not enough room for t+3
    days = [date(2026, 1, 5) + timedelta(days=i) for i in range(n)]
    mkt = {d: 0.01 for d in days}
    stock = dict(mkt)
    with pytest.raises(ModelError, match="event window"):
        event_abnormal_returns(
            [signal("SIG-Y", ("TEST",), days[-1])], {"TEST": stock}, mkt
        )


def test_event_for_unknown_ticker_fails_loudly():
    n = ESTIMATION_WINDOW + 6
    days = [date(2026, 1, 5) + timedelta(days=i) for i in range(n)]
    mkt = {d: 0.01 for d in days}
    with pytest.raises(ModelError, match="no return series"):
        event_abnormal_returns(
            [signal("SIG-Z", ("GHOST",), days[ESTIMATION_WINDOW + 1])], {}, mkt
        )


def test_event_window_shape_constants():
    # The [t-1, t+3] close window is the four returns r_t .. r_t+3; the
    # estimation window is the 63 returns before it. Constants must not drift.
    assert EVENT_AFTER == 3
    assert ESTIMATION_WINDOW == 63


def test_pearson_consistency_with_model_alignment():
    from pipelines.correlation.model import SeriesView

    start = date(2026, 1, 5)
    a = SeriesView("A", {start: 0.01, start + timedelta(days=1): 0.02,
                         start + timedelta(days=2): -0.01})
    b = SeriesView("B", {start + timedelta(days=1): 0.02, start + timedelta(days=2): -0.01})
    xs, ys = a.aligned(b)
    assert xs == [0.02, -0.01]
    assert ys == [0.02, -0.01]
    assert pearson(xs, ys) == pytest.approx(1.0, abs=1e-12)
