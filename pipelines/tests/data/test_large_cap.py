"""Tests for the AS-MC100 Large-Cap 100 data file and its loader.

Contract: exactly 100 unique tickers sourced from the 100-Stock Universe
workbook (sheet sh_7AQ5qtiT); full schema; loader fails loudly on corruption.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from data.large_cap import (
    EXPECTED_COUNT,
    LARGE_CAP_CSV,
    REQUIRED_COLUMNS,
    LargeCapValidationError,
    load_large_cap,
)

HEADER = REQUIRED_COLUMNS


def _write_csv(tmp_path: Path, rows: list[dict[str, str]]) -> Path:
    path = tmp_path / "large_cap_fixture.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(HEADER))
        writer.writeheader()
        writer.writerows(rows)
    return path


def _valid_row(ticker: str = "MSFT") -> dict[str, str]:
    return {c: "x" for c in HEADER} | {
        "ticker": ticker,
        "market_cap_as_of": "2026-11-09",
        "source_url": f"https://example.com/{ticker.lower()}",
    }


class TestCheckedInData:
    def test_real_file_loads_100_rows(self) -> None:
        entries = load_large_cap()
        assert len(entries) == EXPECTED_COUNT
        assert len({e.ticker for e in entries}) == EXPECTED_COUNT

    def test_pstg_is_a_member(self) -> None:
        """PSTG stays a constituent in the data; exclusion happens loudly at
        computation time (see index_exclusions.csv), never by editing data."""
        assert any(e.ticker == "PSTG" for e in load_large_cap())

    def test_every_row_has_market_cap_as_of_2026_11_09(self) -> None:
        entries = load_large_cap()
        assert all(e.market_cap_as_of == "2026-11-09" for e in entries)


class TestLoaderValidation:
    def test_wrong_count_refused(self, tmp_path: Path) -> None:
        rows = [_valid_row(f"T{i:03d}") for i in range(99)]
        path = _write_csv(tmp_path, rows)
        with pytest.raises(LargeCapValidationError, match="exactly 100"):
            load_large_cap(path)

    def test_duplicate_ticker_refused(self, tmp_path: Path) -> None:
        rows = [_valid_row(f"T{i:03d}") for i in range(99)]
        rows.append(_valid_row("T000"))
        path = _write_csv(tmp_path, rows)
        with pytest.raises(LargeCapValidationError, match="duplicate"):
            load_large_cap(path)

    def test_missing_file_refused(self, tmp_path: Path) -> None:
        with pytest.raises(LargeCapValidationError, match="not found"):
            load_large_cap(tmp_path / "absent.csv")

    def test_empty_required_field_refused(self, tmp_path: Path) -> None:
        rows = [_valid_row(f"T{i:03d}") for i in range(99)]
        bad = _valid_row("T099")
        bad["thesis"] = ""
        rows.append(bad)
        path = _write_csv(tmp_path, rows)
        with pytest.raises(LargeCapValidationError, match="thesis"):
            load_large_cap(path)

    def test_bad_date_refused(self, tmp_path: Path) -> None:
        rows = [_valid_row(f"T{i:03d}") for i in range(99)]
        bad = _valid_row("T099")
        bad["market_cap_as_of"] = "11/09/2026"
        rows.append(bad)
        path = _write_csv(tmp_path, rows)
        with pytest.raises(LargeCapValidationError, match="not an ISO date"):
            load_large_cap(path)

    def test_missing_column_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["ticker"])
            writer.writeheader()
        with pytest.raises(LargeCapValidationError, match="missing column"):
            load_large_cap(path)

    def test_default_path_exists(self) -> None:
        assert LARGE_CAP_CSV.is_file()
