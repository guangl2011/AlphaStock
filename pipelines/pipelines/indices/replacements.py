"""Monthly first-Monday replacement feed.

Methodology v3.0: constituent reviews happen on the first Monday of each
month; removals are replaced within the same category. The replacement list
is data, not code — a PR-reviewed CSV with one row per swap:

    effective_date,universe,remove_ticker,add_ticker,reason

Rows are applied by the runner on (and only on) the first trading date at
or after ``effective_date``. An empty file (or absent file) means "no
pending replacements" — the hook accepts that as the normal case.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = ("effective_date", "universe", "remove_ticker", "add_ticker", "reason")


class ReplacementError(ValueError):
    """Raised when the replacement CSV does not meet the contract."""


@dataclass(frozen=True)
class Replacement:
    effective_date: date
    universe: str
    remove_ticker: str
    add_ticker: str
    reason: str


def load_replacements(path: Path | str | None) -> list[Replacement]:
    """Load and validate pending replacements; a missing file means none."""
    if path is None:
        return []
    path = Path(path)
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        missing = [c for c in REQUIRED_COLUMNS if c not in header]
        if missing:
            raise ReplacementError(f"replacement feed missing column(s): {missing}")
        rows = [dict(r) for r in reader]
    out: list[Replacement] = []
    for i, row in enumerate(rows, start=2):
        try:
            effective = date.fromisoformat(row["effective_date"].strip())
        except ValueError as exc:
            raise ReplacementError(
                f"replacement feed line {i}: effective_date {row['effective_date']!r} "
                "is not an ISO date"
            ) from exc
        remove = row["remove_ticker"].strip().upper()
        add = row["add_ticker"].strip().upper()
        if not remove or not add:
            raise ReplacementError(f"replacement feed line {i}: empty remove_ticker/add_ticker")
        if remove == add:
            raise ReplacementError(f"replacement feed line {i}: remove equals add ({remove})")
        out.append(
            Replacement(
                effective_date=effective,
                universe=row["universe"].strip(),
                remove_ticker=remove,
                add_ticker=add,
                reason=row["reason"].strip(),
            )
        )
    return out


def due_on(
    replacements: list[Replacement], universe: str, on_date: date
) -> list[Replacement]:
    """Replacements for ``universe`` taking effect on ``on_date``.

    The runner resolves the first trading date at or after each row's
    ``effective_date`` before calling this, so the comparison here is exact.
    """
    due = [
        r for r in replacements
        if r.universe == universe and r.effective_date == on_date
    ]
    for r in due:
        logger.warning(
            "applying %s replacement: %s -> %s (%s)",
            universe, r.remove_ticker, r.add_ticker, r.reason,
        )
    return due
