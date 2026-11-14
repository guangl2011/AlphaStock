"""Factor construction tests: spreads, alignment, loud failure."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from pipelines.correlation.factors import (
    FACTOR_NAMES,
    FactorError,
    build_factors,
    daily_returns,
    proxy_names,
)
from pipelines.indices.engine import EodRecord

START = date(2026, 1, 5)


def records_for(closes: list[float], start: date = START) -> list[EodRecord]:
    return [EodRecord(start + timedelta(days=i), c, c) for i, c in enumerate(closes)]


def all_proxies() -> dict[str, list[EodRecord]]:
    """Eight proxies over four days, each with its own price path."""
    base: dict[str, list[float]] = {
        "SPY": [100.0, 101.0, 102.0, 103.0],
        "IWM": [50.0, 51.0, 52.0, 51.5],
        "IWC": [30.0, 30.5, 31.0, 31.2],
        "RSP": [45.0, 45.4, 45.9, 46.2],
        "XLU": [60.0, 60.2, 60.1, 60.5],
        "URA": [25.0, 25.6, 25.2, 25.9],
        "GRID": [20.0, 20.4, 20.9, 21.0],
        "ROBO": [35.0, 35.5, 35.2, 35.8],
    }
    return {ticker: records_for(closes) for ticker, closes in base.items()}


def test_factor_names_are_complete():
    assert FACTOR_NAMES == (
        "BREADTH", "GRID", "MICRO", "MKT", "NUKE", "ROBO", "SIZE", "UTIL"
    )


def test_mkt_is_spy_daily_return():
    build = build_factors(all_proxies())
    assert build.series["MKT"] == {
        date(2026, 1, 6): 101.0 / 100.0 - 1.0,
        date(2026, 1, 7): 102.0 / 101.0 - 1.0,
        date(2026, 1, 8): 103.0 / 102.0 - 1.0,
    }


def test_thematic_factor_is_single_proxy_return():
    proxies = all_proxies()
    build = build_factors(proxies)
    assert build.series["NUKE"] == daily_returns(proxies["URA"])
    assert build.series["GRID"] == daily_returns(proxies["GRID"])


def test_spread_factor_is_leg_difference_on_common_dates():
    proxies = all_proxies()
    # IWM loses its last day: the spread exists only where both legs do.
    proxies["IWM"] = proxies["IWM"][:3]
    build = build_factors(proxies)
    size = build.series["SIZE"]
    assert sorted(size) == [date(2026, 1, 6), date(2026, 1, 7)]
    for day in sorted(size):
        spy_ret = by_date(proxies["SPY"])[day].close / prev_close(proxies["SPY"], day) - 1.0
        iwm_ret = by_date(proxies["IWM"])[day].close / prev_close(proxies["IWM"], day) - 1.0
        assert size[day] == pytest.approx(iwm_ret - spy_ret)


def by_date(records: list[EodRecord]) -> dict[date, EodRecord]:
    return {r.date: r for r in records}


def prev_close(records: list[EodRecord], day: date) -> float:
    earlier = [r.close for r in records if r.date < day]
    return earlier[-1]


def test_alignment_gaps_report_dropped_dates():
    proxies = all_proxies()
    proxies["IWM"] = proxies["IWM"][:3]
    build = build_factors(proxies)
    assert build.alignment_gaps["SIZE"] == [date(2026, 1, 8).isoformat()]


def test_missing_proxy_fails_loudly():
    proxies = all_proxies()
    del proxies["RSP"]
    with pytest.raises(FactorError, match="needs RSP records"):
        build_factors(proxies)


def test_empty_proxy_fails_loudly():
    proxies = all_proxies()
    proxies["XLU"] = []
    with pytest.raises(FactorError, match="needs XLU records"):
        build_factors(proxies)


def test_non_positive_close_fails_loudly():
    with pytest.raises(FactorError, match="non-positive close"):
        daily_returns(records_for([100.0, 0.0]))


def test_proxy_names_are_all_documented_in_proxy_map():
    # Every factor proxy must be a documented PROXY_MAP entry — the mapping
    # is data, never implied.
    assert proxy_names() <= {"SPY", "IWM", "IWC", "RSP", "XLU", "URA", "GRID", "ROBO"}
