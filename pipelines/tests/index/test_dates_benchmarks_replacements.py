"""Tests for dates, benchmarks, and the replacement feed."""

from __future__ import annotations

from datetime import date

import pytest

from pipelines.indices.benchmarks import (
    PROXY_MAP,
    BenchmarkSeries,
    comparison_date_gaps,
    comparison_rows,
    rebase_proxy,
)
from pipelines.indices.dates import first_monday, rebalance_dates
from pipelines.indices.engine import EngineError, EodRecord
from pipelines.indices.replacements import ReplacementError, due_on, load_replacements


class TestRebalanceDates:
    def test_last_trading_day_of_each_quarter_month(self) -> None:
        dates = [
            date(2027, 3, 28),  # Sunday — not a trading day, exercises max()
            date(2027, 3, 31),
            date(2027, 4, 1),
            date(2027, 6, 30),
            date(2027, 9, 30),
            date(2027, 12, 31),
        ]
        assert rebalance_dates(dates) == {
            date(2027, 3, 31), date(2027, 6, 30),
            date(2027, 9, 30), date(2027, 12, 31),
        }

    def test_non_quarter_months_ignored(self) -> None:
        dates = [date(2027, 1, 29), date(2027, 2, 26)]
        assert rebalance_dates(dates) == set()

    def test_empty_dates_refused(self) -> None:
        with pytest.raises(ValueError, match="no trading dates"):
            rebalance_dates([])

    def test_first_monday(self) -> None:
        assert first_monday(2026, 12) == date(2026, 12, 7)
        assert first_monday(2026, 11) == date(2026, 11, 2)


class TestBenchmarks:
    def test_rebase_to_1000_on_base_date(self) -> None:
        records = [
            EodRecord(date(2026, 11, 6), 600.0, 600.0),
            EodRecord(date(2026, 11, 9), 606.0, 606.0),
        ]
        series = rebase_proxy("SPY", records, date(2026, 11, 6))
        assert series.levels[date(2026, 11, 6)] == 1000.0
        assert series.levels[date(2026, 11, 9)] == pytest.approx(1010.0, abs=1e-9)

    def test_unknown_proxy_refused(self) -> None:
        with pytest.raises(EngineError, match="extend PROXY_MAP"):
            rebase_proxy("SPX", [], date(2026, 11, 6))

    def test_missing_base_close_refused(self) -> None:
        records = [EodRecord(date(2026, 11, 9), 600.0, 600.0)]
        with pytest.raises(EngineError, match="no close on base date"):
            rebase_proxy("SPY", records, date(2026, 11, 6))

    def test_proxy_map_documents_every_benchmark(self) -> None:
        for name in ("SPY", "IWM", "IWC", "QQQ", "RSP", "XLU", "URA", "GRID", "ROBO"):
            assert name in PROXY_MAP
            assert "proxy" in PROXY_MAP[name].lower()

    def test_comparison_rows_pair_index_and_proxies(self) -> None:
        idx = {date(2026, 11, 6): 1000.0, date(2026, 11, 9): 1010.0}
        bench = BenchmarkSeries("SPY", "S&P 500 (ETF proxy)", {
            date(2026, 11, 6): 1000.0, date(2026, 11, 9): 1005.0,
        })
        rows = comparison_rows("AS-100", idx, [bench])
        assert [(r["series"], r["level"]) for r in rows] == [
            ("AS-100", 1000.0), ("SPY", 1000.0),
            ("AS-100", 1010.0), ("SPY", 1005.0),
        ]

    def test_comparison_gaps_are_surfaced_not_filled(self) -> None:
        idx = {date(2026, 11, 6): 1000.0, date(2026, 11, 9): 1010.0}
        bench = BenchmarkSeries("SPY", "S&P 500 (ETF proxy)", {date(2026, 11, 6): 1000.0})
        assert comparison_date_gaps(idx, [bench]) == ["2026-11-09"]


class TestReplacements:
    def _csv(self, tmp_path, text: str) -> object:
        path = tmp_path / "replacements.csv"
        path.write_text(text, encoding="utf-8")
        return path

    def test_missing_file_means_no_replacements(self, tmp_path) -> None:
        assert load_replacements(tmp_path / "absent.csv") == []
        assert load_replacements(None) == []

    def test_load_and_due_on(self, tmp_path) -> None:
        path = self._csv(tmp_path, (
            "effective_date,universe,remove_ticker,add_ticker,reason\n"
            "2027-04-05,Mixed,BBB,CCC,same-category replacement\n"
        ))
        reps = load_replacements(path)
        assert len(reps) == 1
        assert reps[0].remove_ticker == "BBB" and reps[0].add_ticker == "CCC"
        assert due_on(reps, "Mixed", date(2027, 4, 5)) == reps
        assert due_on(reps, "Mixed", date(2027, 4, 6)) == []
        assert due_on(reps, "LargeCap100", date(2027, 4, 5)) == []

    def test_bad_date_is_loud(self, tmp_path) -> None:
        path = self._csv(tmp_path, (
            "effective_date,universe,remove_ticker,add_ticker,reason\n"
            "not-a-date,Mixed,BBB,CCC,x\n"
        ))
        with pytest.raises(ReplacementError, match="not an ISO date"):
            load_replacements(path)

    def test_self_replacement_refused(self, tmp_path) -> None:
        path = self._csv(tmp_path, (
            "effective_date,universe,remove_ticker,add_ticker,reason\n"
            "2027-04-05,Mixed,BBB,BBB,x\n"
        ))
        with pytest.raises(ReplacementError, match="remove equals add"):
            load_replacements(path)

    def test_missing_column_is_loud(self, tmp_path) -> None:
        path = self._csv(tmp_path, "effective_date,universe,remove_ticker\n")
        with pytest.raises(ReplacementError, match="missing column"):
            load_replacements(path)
