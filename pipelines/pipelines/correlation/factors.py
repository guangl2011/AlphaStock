"""Factor series: daily-return spreads of the benchmark ETF proxies.

Every factor is built from ETF proxies already in
``pipelines.indices.benchmarks.PROXY_MAP`` — licensed index values are
never fetched or republished. Factors are daily *returns* (not levels):
spreads are differences of same-day returns, so the numbers are
stationary and comparable across proxies.

Determinism: a factor is a pure function of the input EOD records; dates
are iterated in sorted order and results are plain floats.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from ..indices.benchmarks import PROXY_MAP
from ..indices.engine import EodRecord

#: Single-ETF factors: the factor's daily return is the proxy's daily return.
RETURN_FACTORS: dict[str, str] = {
    "MKT": "SPY",
    "UTIL": "XLU",
    "NUKE": "URA",
    "GRID": "GRID",
    "ROBO": "ROBO",
}

#: Spread factors: factor return = leg_b return − leg_a return, same day.
SPREAD_FACTORS: dict[str, tuple[str, str]] = {
    "SIZE": ("SPY", "IWM"),
    "MICRO": ("SPY", "IWC"),
    "BREADTH": ("SPY", "RSP"),
}

#: All factors the model defines, in canonical (sorted) order.
FACTOR_NAMES: tuple[str, ...] = tuple(
    sorted([*RETURN_FACTORS, *SPREAD_FACTORS])
)

#: Factors entering the index OLS exposure regression (spec: MKT + BREADTH
#: + thematic comparators; SIZE/MICRO are correlation-only).
REGRESSION_FACTORS: tuple[str, ...] = ("MKT", "BREADTH", "UTIL", "NUKE", "GRID", "ROBO")


class FactorError(ValueError):
    """Raised when the factor inputs are missing or inconsistent."""


@dataclass(frozen=True)
class FactorBuild:
    """The factor series plus the alignment report a run must surface."""

    series: dict[str, dict[date, float]]
    #: Dates dropped from a spread factor because one leg had no return.
    alignment_gaps: dict[str, list[str]]

    @property
    def definitions(self) -> dict[str, str]:
        """Human-readable construction of each factor, for run metadata."""
        out: dict[str, str] = {}
        for name, proxy in sorted(RETURN_FACTORS.items()):
            out[name] = f"{proxy} daily return"
        for name, (base, leg) in sorted(SPREAD_FACTORS.items()):
            out[name] = f"{leg} daily return minus {base} daily return"
        return out


def daily_returns(records: list[EodRecord]) -> dict[date, float]:
    """Day-over-day close-to-close returns from sorted EOD records."""
    out: dict[date, float] = {}
    ordered = sorted(records, key=lambda r: r.date)
    # Validate every close before any division: a zero or negative close
    # (corrupt row, bad split) would corrupt the ratio silently.
    for record in ordered:
        if record.close <= 0:
            raise FactorError(f"non-positive close for {record.date}: {record.close}")
    for prev, cur in zip(ordered, ordered[1:], strict=False):
        out[cur.date] = cur.close / prev.close - 1.0
    return out


def build_factors(records: Mapping[str, list[EodRecord]]) -> FactorBuild:
    """Build every factor in ``FACTOR_NAMES`` from ETF proxy records.

    Raises :class:`FactorError` when a proxy the factor set depends on has
    no records at all — a silently missing factor would corrupt every
    downstream number.
    """
    returns = {ticker: daily_returns(recs) for ticker, recs in records.items()}
    series: dict[str, dict[date, float]] = {}
    gaps: dict[str, list[str]] = {}

    for name, proxy in RETURN_FACTORS.items():
        if not records.get(proxy):
            raise FactorError(f"factor {name} needs {proxy} records; none present")
        series[name] = dict(returns[proxy])

    for name, (base, leg) in SPREAD_FACTORS.items():
        for proxy in (base, leg):
            if not records.get(proxy):
                raise FactorError(f"factor {name} needs {proxy} records; none present")
        common = sorted(set(returns[base]) & set(returns[leg]))
        dropped = sorted(set(returns[base]) ^ set(returns[leg]))
        gaps[name] = [d.isoformat() for d in dropped]
        series[name] = {d: returns[leg][d] - returns[base][d] for d in common}

    return FactorBuild(series=series, alignment_gaps=gaps)


def proxy_names() -> set[str]:
    """Every Tiingo symbol the factor set consumes (all from PROXY_MAP)."""
    used = set(RETURN_FACTORS.values())
    for base, leg in SPREAD_FACTORS.values():
        used.update((base, leg))
    unknown = used - set(PROXY_MAP)
    if unknown:
        raise FactorError(
            f"factor proxies {sorted(unknown)} are not in PROXY_MAP — extend it explicitly"
        )
    return used
