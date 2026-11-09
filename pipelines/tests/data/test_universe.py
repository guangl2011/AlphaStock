"""Tests for the coverage universe data and its loader.

Contract: exactly 100 unique US-listed tickers, full schema, and a loader that
fails loudly on any corruption (count, uniqueness, schema, date format).
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest

from data.universe import (
    EXPECTED_COUNT,
    REQUIRED_COLUMNS,
    UNIVERSE_CSV,
    UniverseEntry,
    UniverseValidationError,
    load_universe,
)

HEADER = REQUIRED_COLUMNS


def _write_csv(tmp_path: Path, rows: list[dict[str, str]], header: list[str] | None = None) -> Path:
    path = tmp_path / "universe_fixture.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header or list(HEADER))
        writer.writeheader()
        writer.writerows(rows)
    return path


def _valid_row(ticker: str = "NVDA") -> dict[str, str]:
    return {
        "ticker": ticker,
        "company": "Test Corp",
        "category": "AI & Compute",
        "themes": "Data Centers",
        "exposure_type": "Direct",
        "market_cap": "1.00T",
        "market_cap_as_of": "2026-11-06",
        "thesis": "Test thesis.",
        "key_risk": "Test risk.",
        "source_url": "https://example.com/" + ticker.lower(),
    }


def _valid_rows(n: int) -> list[dict[str, str]]:
    return [_valid_row(f"T{i:03d}") for i in range(n)]


class TestRepoUniverseData:
    """The shipped universe.csv and watchlist_top10.csv meet the contract."""

    def test_universe_file_exists(self) -> None:
        assert UNIVERSE_CSV.is_file()

    def test_exactly_100_unique_tickers(self) -> None:
        entries = load_universe()
        assert len(entries) == EXPECTED_COUNT
        tickers = [e.ticker for e in entries]
        assert len(set(tickers)) == EXPECTED_COUNT

    def test_schema_columns(self) -> None:
        with UNIVERSE_CSV.open(newline="", encoding="utf-8") as f:
            header = next(csv.reader(f))
        assert header == list(HEADER)

    def test_entries_parse_into_typed_dataclass(self) -> None:
        entries = load_universe()
        assert all(isinstance(e, UniverseEntry) for e in entries)
        vst = next(e for e in entries if e.ticker == "VST")
        assert vst.market_cap_as_of == date(2026, 11, 6)
        assert vst.source_url.startswith("https://")

    def test_watchlist_top10_is_opt_in_header_only(self) -> None:
        path = UNIVERSE_CSV.parent / "watchlist_top10.csv"
        assert path.is_file()
        rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
        assert rows[0] == ["ticker", "rank", "rationale"]
        assert len(rows) == 1  # header only — the curated override is opt-in


class TestLoaderValidation:
    """The loader raises loudly on corrupted fixtures."""

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(UniverseValidationError, match="not found"):
            load_universe(tmp_path / "nope.csv")

    def test_wrong_count(self, tmp_path: Path) -> None:
        path = _write_csv(tmp_path, _valid_rows(99))
        with pytest.raises(UniverseValidationError, match="exactly 100"):
            load_universe(path)

    def test_duplicate_ticker(self, tmp_path: Path) -> None:
        rows = _valid_rows(100)
        rows[99] = dict(rows[0])  # same ticker as row 0
        path = _write_csv(tmp_path, rows)
        with pytest.raises(UniverseValidationError, match="duplicate ticker"):
            load_universe(path)

    def test_missing_column(self, tmp_path: Path) -> None:
        header = [c for c in HEADER if c != "thesis"]
        rows = [{k: v for k, v in r.items() if k != "thesis"} for r in _valid_rows(100)]
        path = _write_csv(tmp_path, rows, header=header)
        with pytest.raises(UniverseValidationError, match="missing column"):
            load_universe(path)

    def test_empty_required_field(self, tmp_path: Path) -> None:
        rows = _valid_rows(100)
        rows[50]["key_risk"] = ""
        path = _write_csv(tmp_path, rows)
        with pytest.raises(UniverseValidationError, match="empty required field"):
            load_universe(path)

    def test_bad_as_of_date(self, tmp_path: Path) -> None:
        rows = _valid_rows(100)
        rows[10]["market_cap_as_of"] = "November 2026"
        path = _write_csv(tmp_path, rows)
        with pytest.raises(UniverseValidationError, match="not an ISO date"):
            load_universe(path)

    def test_missing_ticker(self, tmp_path: Path) -> None:
        rows = _valid_rows(100)
        rows[7]["ticker"] = "  "
        path = _write_csv(tmp_path, rows)
        with pytest.raises(UniverseValidationError, match="missing ticker"):
            load_universe(path)


def _roundtrip_ok() -> bool:  # pragma: no cover — guard for accidental import issues
    try:
        load_universe()
        return True
    except UniverseValidationError:
        return False


def test_repo_universe_loads_clean() -> None:
    assert _roundtrip_ok()
