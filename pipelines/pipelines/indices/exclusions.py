"""Documented constituent exclusions.

A ticker with no price history is never dropped silently. ``index_exclusions.csv``
is the PR-reviewed record of every ticker the pipeline must exclude, why, and
when the gap was first observed. At computation time the loader re-checks
each exclusion against live data: a documented exclusion that starts
returning rows (or an undocumented one) is surfaced loudly.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# The CSV lives in the repo's `data` package (pipelines/data), three levels
# above this file — the same root compute.py puts on sys.path for `data.*` imports.
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
EXCLUSIONS_CSV = DATA_DIR / "index_exclusions.csv"

REQUIRED_COLUMNS = ("ticker", "universe", "reason", "first_observed", "source")


class ExclusionError(ValueError):
    """Raised when index_exclusions.csv does not meet the contract."""


@dataclass(frozen=True)
class Exclusion:
    ticker: str
    universe: str
    reason: str
    first_observed: str
    source: str


def load_exclusions(path: Path | str = EXCLUSIONS_CSV) -> list[Exclusion]:
    """Load documented exclusions; a missing file means none (not an error)."""
    path = Path(path)
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        missing = [c for c in REQUIRED_COLUMNS if c not in header]
        if missing:
            raise ExclusionError(f"index_exclusions.csv missing column(s): {missing}")
        out = []
        for i, row in enumerate(reader, start=2):
            for field in REQUIRED_COLUMNS:
                if not row.get(field, "").strip():
                    raise ExclusionError(
                        f"index_exclusions.csv line {i}: empty required field {field!r}"
                    )
            out.append(
                Exclusion(
                    ticker=row["ticker"].strip().upper(),
                    universe=row["universe"].strip(),
                    reason=row["reason"].strip(),
                    first_observed=row["first_observed"].strip(),
                    source=row["source"].strip(),
                )
            )
        return out


def reconcile(
    exclusions: list[Exclusion],
    universe: str,
    tickers_with_data: set[str],
    tickers_missing: set[str],
) -> tuple[list[Exclusion], list[Exclusion]]:
    """Cross-check documented exclusions against what the fetch actually saw.

    Returns (effective, surprises):

    * ``effective`` — documented exclusions for this universe whose tickers
      still have no data. Each is logged at WARNING level (loud by contract).
    * ``surprises`` — any mismatch: a documented exclusion that now has data,
      or a ticker missing data with no documented exclusion. Both are logged
      at WARNING and recorded in run metadata so they cannot be missed.

    The caller must treat a non-empty ``surprises`` list as a run-review
    trigger: the universe composition contract has changed.
    """
    documented = {e.ticker: e for e in exclusions if e.universe == universe}

    effective: list[Exclusion] = []
    for ticker, exclusion in sorted(documented.items()):
        if ticker in tickers_missing:
            logger.warning(
                "EXCLUSION %s [%s]: %s has no Tiingo price history and is excluded "
                "from %s computation — documented %s (%s). The index computes over "
                "the remaining constituents; review this exclusion monthly.",
                ticker, universe, ticker, universe, exclusion.first_observed,
                exclusion.reason,
            )
            effective.append(exclusion)
        else:
            logger.warning(
                "EXCLUSION STALE %s [%s]: %s is documented as excluded but data is "
                "now available — update index_exclusions.csv and reconsider inclusion.",
                ticker, universe, ticker,
            )

    surprises = [
        Exclusion(
            t, universe, "no data returned by Tiingo (not documented)", "run-time", "pipeline"
        )
        for t in sorted(tickers_missing - set(documented))
    ]
    surprises += [
        Exclusion(
            t, universe, "documented exclusion but data is now available", "run-time", "pipeline"
        )
        for t in sorted(set(documented) & tickers_with_data)
    ]
    for s in surprises:
        logger.warning("EXCLUSION SURPRISE %s [%s]: %s", s.ticker, s.universe, s.reason)
    return effective, surprises
