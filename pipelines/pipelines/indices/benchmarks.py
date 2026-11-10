"""Benchmark ETF proxies and the comparison export.

Licensed index values (S&P, Russell) are never fetched. Instead each
benchmark is represented by a liquid ETF whose EOD history comes from Tiingo
through the same client and cache as the constituents. The proxy mapping is
stated in every output's metadata — never implied.

Each proxy is based to 1000 on the same base date as the index it is
compared against, so percentage moves are directly comparable.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from .engine import BASE_LEVEL, EngineError, EodRecord

#: ETF proxy per benchmark. Keys are Tiingo symbols; values name the index
#: each ETF stands in for. This mapping is data, surfaced verbatim in output
#: metadata.
PROXY_MAP: dict[str, str] = {
    "SPY": "S&P 500 (ETF proxy)",
    "IWM": "Russell 2000 (ETF proxy)",
    "IWC": "Russell Microcap (ETF proxy)",
    "QQQ": "Nasdaq-100 (ETF proxy)",
    "RSP": "S&P 500 Equal Weight (ETF proxy)",
    "XLU": "Utilities Select Sector (thematic comparator, ETF proxy)",
    "URA": "Uranium (thematic comparator, ETF proxy)",
    "GRID": "Global Clean Energy / Grid (thematic comparator, ETF proxy)",
    "ROBO": "Robotics & AI (thematic comparator, ETF proxy)",
}

#: The benchmark set the methodology compares each index against.
INDEX_BENCHMARKS: dict[str, tuple[str, ...]] = {
    "AS-100": ("SPY", "IWM", "IWC"),
    "AS-MC100": ("SPY", "QQQ", "RSP"),
}


@dataclass(frozen=True)
class BenchmarkSeries:
    """One proxy rebased to BASE_LEVEL on the base date."""

    proxy: str
    name: str
    levels: dict[date, float]


def rebase_proxy(
    proxy: str, records: list[EodRecord], base_date: date
) -> BenchmarkSeries:
    """Rebase an ETF's closes to 1000 at ``base_date``.

    Raises :class:`EngineError` when the proxy has no close on the base date
    — silently skipping a benchmark would corrupt the comparison view.
    """
    if proxy not in PROXY_MAP:
        raise EngineError(f"unknown benchmark proxy {proxy!r} — extend PROXY_MAP explicitly")
    closes = {r.date: r.close for r in records}
    if base_date not in closes:
        raise EngineError(f"benchmark proxy {proxy} has no close on base date {base_date}")
    base_close = closes[base_date]
    levels = {d: round(BASE_LEVEL * c / base_close, 6) for d, c in sorted(closes.items())}
    return BenchmarkSeries(proxy=proxy, name=PROXY_MAP[proxy], levels=levels)


def comparison_rows(
    index_id: str,
    index_levels: Mapping[date, float],
    benchmarks: list[BenchmarkSeries],
) -> list[dict[str, str | float]]:
    """Long-format rows for the comparison view: index and proxies side by side.

    Dates without a level on every series are dropped (no implicit fill);
    the union of series dates minus the intersection is reported by the
    caller in run metadata as ``comparison_date_gaps``.
    """
    common = set(index_levels)
    for series in benchmarks:
        common &= set(series.levels)
    rows: list[dict[str, str | float]] = []
    for d in sorted(common):
        rows.append({"date": d.isoformat(), "series": index_id, "level": index_levels[d]})
        for series in benchmarks:
            rows.append(
                {"date": d.isoformat(), "series": series.proxy, "level": series.levels[d]}
            )
    return rows


def comparison_date_gaps(
    index_levels: Mapping[date, float], benchmarks: list[BenchmarkSeries]
) -> list[str]:
    """ISO dates present in some but not all series — loud, not dropped silently."""
    all_dates: set[date] = set(index_levels)
    for series in benchmarks:
        all_dates |= set(series.levels)
    gaps = [
        d.isoformat()
        for d in sorted(all_dates)
        if any(d not in s.levels for s in benchmarks) or d not in index_levels
    ]
    return gaps
