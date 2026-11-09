"""Loader for the AlphaStock coverage universe.

The universe is data, not code: ``universe.csv`` is PR-reviewed and versioned
in the repo. Loading validates loudly — a corrupted or stale universe file must
fail a scan, never silently shrink coverage.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent
UNIVERSE_CSV = DATA_DIR / "universe.csv"
WATCHLIST_CSV = DATA_DIR / "watchlist_top10.csv"

EXPECTED_COUNT = 100

REQUIRED_COLUMNS = (
    "ticker",
    "company",
    "category",
    "themes",
    "exposure_type",
    "market_cap",
    "market_cap_as_of",
    "thesis",
    "key_risk",
    "source_url",
)

# Fields that must be non-empty on every row. source_url is also required —
# provenance is non-negotiable per the build spec.
_REQUIRED_FIELDS = (
    "ticker",
    "company",
    "category",
    "themes",
    "exposure_type",
    "market_cap",
    "market_cap_as_of",
    "thesis",
    "key_risk",
    "source_url",
)


class UniverseValidationError(ValueError):
    """Raised when universe.csv does not meet the coverage contract."""


@dataclass(frozen=True)
class UniverseEntry:
    ticker: str
    company: str
    category: str
    themes: str
    exposure_type: str
    market_cap: str
    market_cap_as_of: date
    thesis: str
    key_risk: str
    source_url: str


def _parse_as_of(value: str, ticker: str) -> date:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise UniverseValidationError(
            f"universe.csv row {ticker!r}: market_cap_as_of {value!r} is not an ISO date"
        ) from exc


def _validate_rows(rows: list[dict[str, str]]) -> list[UniverseEntry]:
    entries: list[UniverseEntry] = []
    seen: set[str] = set()
    for i, row in enumerate(rows, start=2):  # line 1 is the header
        ticker = row.get("ticker", "").strip()
        if not ticker:
            raise UniverseValidationError(f"universe.csv line {i}: missing ticker")
        if ticker in seen:
            raise UniverseValidationError(f"universe.csv line {i}: duplicate ticker {ticker!r}")
        seen.add(ticker)
        missing = [f for f in _REQUIRED_FIELDS if not row.get(f, "").strip()]
        if missing:
            raise UniverseValidationError(
                f"universe.csv row {ticker!r}: empty required field(s) {missing}"
            )
        entries.append(
            UniverseEntry(
                ticker=ticker,
                company=row["company"].strip(),
                category=row["category"].strip(),
                themes=row["themes"].strip(),
                exposure_type=row["exposure_type"].strip(),
                market_cap=row["market_cap"].strip(),
                market_cap_as_of=_parse_as_of(row["market_cap_as_of"], ticker),
                thesis=row["thesis"].strip(),
                key_risk=row["key_risk"].strip(),
                source_url=row["source_url"].strip(),
            )
        )
    if len(entries) != EXPECTED_COUNT:
        raise UniverseValidationError(
            f"universe.csv must contain exactly {EXPECTED_COUNT} rows, found {len(entries)}"
        )
    return entries


def load_universe(path: Path | str = UNIVERSE_CSV) -> list[UniverseEntry]:
    """Load and validate the coverage universe.

    Raises :class:`UniverseValidationError` on any schema, count, uniqueness,
    or date-format violation.
    """
    path = Path(path)
    if not path.is_file():
        raise UniverseValidationError(f"universe file not found: {path}")
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        missing = [c for c in REQUIRED_COLUMNS if c not in header]
        if missing:
            raise UniverseValidationError(f"universe.csv missing column(s): {missing}")
        rows = [dict(r) for r in reader]
    return _validate_rows(rows)
