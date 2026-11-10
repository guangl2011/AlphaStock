"""Loader for the AS-MC100 (Large-Cap 100) constituent list.

The universe is data, not code: ``large_cap_100.csv`` is sourced from the
100-Stock Universe workbook (sheet ``sh_7AQ5qtiT``, retrieved 2026-11-10)
and is PR-reviewed and versioned like ``universe.csv``. Loading validates
loudly — a corrupted or short file must fail a run, never silently shrink
the index.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent
LARGE_CAP_CSV = DATA_DIR / "large_cap_100.csv"

#: AS-MC100 is a 100-stock universe by construction. One member (PSTG) has
#: no Tiingo price history and is excluded at computation time — loudly, via
#: index_exclusions.csv — so the CSV keeps all 100 constituents.
EXPECTED_COUNT = 100

REQUIRED_COLUMNS = (
    "ticker",
    "company",
    "category",
    "tier",
    "themes",
    "exposure_type",
    "market_cap",
    "market_cap_as_of",
    "thesis",
    "key_risk",
    "source_url",
)

_REQUIRED_FIELDS = REQUIRED_COLUMNS


class LargeCapValidationError(ValueError):
    """Raised when large_cap_100.csv does not meet the coverage contract."""


@dataclass(frozen=True)
class LargeCapEntry:
    ticker: str
    company: str
    category: str
    tier: str
    themes: str
    exposure_type: str
    market_cap: str
    market_cap_as_of: str
    thesis: str
    key_risk: str
    source_url: str


def _validate_rows(rows: list[dict[str, str]]) -> list[LargeCapEntry]:
    entries: list[LargeCapEntry] = []
    seen: set[str] = set()
    for i, row in enumerate(rows, start=2):  # line 1 is the header
        ticker = row.get("ticker", "").strip().upper()
        if not ticker:
            raise LargeCapValidationError(f"large_cap_100.csv line {i}: missing ticker")
        if ticker in seen:
            raise LargeCapValidationError(
                f"large_cap_100.csv line {i}: duplicate ticker {ticker!r}"
            )
        seen.add(ticker)
        missing = [f for f in _REQUIRED_FIELDS if not row.get(f, "").strip()]
        if missing:
            raise LargeCapValidationError(
                f"large_cap_100.csv row {ticker!r}: empty required field(s) {missing}"
            )
        try:
            datetime.strptime(row["market_cap_as_of"].strip(), "%Y-%m-%d")
        except ValueError as exc:
            raise LargeCapValidationError(
                f"large_cap_100.csv row {ticker!r}: market_cap_as_of "
                f"{row['market_cap_as_of']!r} is not an ISO date"
            ) from exc
        entries.append(LargeCapEntry(**{f: row[f].strip() for f in REQUIRED_COLUMNS}))
    if len(entries) != EXPECTED_COUNT:
        raise LargeCapValidationError(
            f"large_cap_100.csv must contain exactly {EXPECTED_COUNT} rows, "
            f"found {len(entries)}"
        )
    return entries


def load_large_cap(path: Path | str = LARGE_CAP_CSV) -> list[LargeCapEntry]:
    """Load and validate the AS-MC100 constituent list.

    Raises :class:`LargeCapValidationError` on any schema, count, uniqueness,
    or date-format violation.
    """
    path = Path(path)
    if not path.is_file():
        raise LargeCapValidationError(f"large-cap universe file not found: {path}")
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        missing = [c for c in REQUIRED_COLUMNS if c not in header]
        if missing:
            raise LargeCapValidationError(f"large_cap_100.csv missing column(s): {missing}")
        rows = [dict(r) for r in reader]
    return _validate_rows(rows)
