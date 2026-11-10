"""Trading-calendar helpers derived from observed trading dates.

The pipeline owns no market calendar: trading days are the dates actually
present in the (fetched or fixture) price data, so calendar drift can never
silently desynchronize rebalance logic from the data.
"""

from __future__ import annotations

from datetime import date

# Quarterly rebalance months per methodology v3.0.
REBALANCE_MONTHS = frozenset({3, 6, 9, 12})


def last_trading_day_per_month(dates: list[date]) -> dict[tuple[int, int], date]:
    """Map (year, month) -> last observed trading date of that month.

    Raises ValueError on an empty input; dates need not be sorted.
    """
    if not dates:
        raise ValueError("no trading dates observed")
    out: dict[tuple[int, int], date] = {}
    for d in dates:
        key = (d.year, d.month)
        if key not in out or d > out[key]:
            out[key] = d
    return out


def rebalance_dates(dates: list[date]) -> set[date]:
    """The last trading day of every Mar/Jun/Sep/Dec present in the data."""
    return {
        last for (year, month), last in last_trading_day_per_month(dates).items()
        if month in REBALANCE_MONTHS
    }


def first_monday(year: int, month: int) -> date:
    """The first Monday of a month — the monthly review date (methodology v3.0)."""
    d = date(year, month, 1)
    offset = (7 - d.weekday()) % 7  # Monday is weekday 0
    return d.fromordinal(d.toordinal() + offset)
