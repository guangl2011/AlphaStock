"""End-to-end pipeline tests on the checked-in fixture closes (methodology v4.0).

The fixture (tests/fixtures/index/base_closes.csv) carries EOD closes for every
constituent of the three indices and every benchmark proxy, 2026-11-06 through
2026-11-13. These tests prove the base anchors offline: AS-100 (Large-Cap) is
exactly 1000.0 on 2026-11-09; AS-MCG100 and AS-MCB100 are exactly 1000.0 on
their 2026-11-13 construction close.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from pipelines.indices.__main__ import load_fixture_records
from pipelines.indices.benchmarks import INDEX_BENCHMARKS, BenchmarkSeries, rebase_proxy
from pipelines.indices.compute import (
    BASE_DATES,
    INDEX_IDS,
    compute_index,
    levels_csv,
    load_compositions,
    metadata_json,
    write_outputs,
)
from pipelines.indices.exclusions import load_exclusions
from pipelines.indices.replacements import load_replacements

FIXTURE_CSV = Path(__file__).resolve().parents[1] / "fixtures" / "index" / "base_closes.csv"

AS100_BASE = date(2026, 11, 9)
MIXED_BASE = date(2026, 11, 13)


@pytest.fixture(scope="module")
def records():
    return load_fixture_records(FIXTURE_CSV)


@pytest.fixture(scope="module")
def compositions():
    return load_compositions()


@pytest.fixture(scope="module")
def computed(records, compositions):
    exclusions = load_exclusions()
    return [
        compute_index(
            index_id,
            compositions[index_id],
            records,
            replacements=load_replacements(None),
            exclusions=exclusions,
        )
        for index_id in INDEX_IDS
    ]


def _index(computed, index_id):
    return next(i for i in computed if i.index_id == index_id)


class TestThreeIndexModel:
    def test_three_indices_in_canonical_order(self) -> None:
        assert INDEX_IDS == ("AS-100", "AS-MCG100", "AS-MCB100")

    def test_base_anchors_are_exactly_1000(self, computed) -> None:
        assert _index(computed, "AS-100").levels[AS100_BASE] == 1000.0
        assert _index(computed, "AS-MCG100").levels[MIXED_BASE] == 1000.0
        assert _index(computed, "AS-MCB100").levels[MIXED_BASE] == 1000.0

    def test_base_dates_are_the_construction_closes(self, records) -> None:
        assert records
        assert BASE_DATES == {
            "AS-100": AS100_BASE, "AS-MCG100": MIXED_BASE, "AS-MCB100": MIXED_BASE,
        }

    def test_mixed_cap_indices_never_hold_an_as100_name(self, compositions) -> None:
        large = set(compositions["AS-100"])
        assert not large & set(compositions["AS-MCG100"])
        assert not large & set(compositions["AS-MCB100"])
        # MCG and MCB are weightings of one sub-$10B opportunity set: they share.
        assert set(compositions["AS-MCG100"]) & set(compositions["AS-MCB100"])

    def test_every_index_has_100_constituents(self, computed) -> None:
        for index in computed:
            assert len(index.constituents) == 100, index.index_id


class TestPSTGMappedToP:
    """Methodology decision 2026-11-14: PSTG's "missing history" was a ticker
    change (Everpure, NYSE: P from 2026-04-17). The constituent is P and the
    exclusion is retired — a stale ticker must never be excluded quietly again."""

    def test_p_computes_and_nothing_is_excluded(self, computed) -> None:
        mc = _index(computed, "AS-100")
        assert "P" in mc.constituents
        assert "PSTG" not in mc.constituents
        assert len(mc.constituents) == 100
        assert mc.excluded == []
        assert mc.levels[AS100_BASE] == 1000.0

    def test_universe_and_exclusion_log_reflect_the_mapping(self) -> None:
        from data.large_cap import load_large_cap

        entries = load_large_cap()
        assert len(entries) == 100
        tickers = {e.ticker for e in entries}
        assert "P" in tickers and "PSTG" not in tickers
        assert load_exclusions() == []

    def test_undocumented_gap_fails_the_run(self, records) -> None:
        """A missing constituent with no documented exclusion never computes quietly."""
        from pipelines.indices.engine import EngineError

        # AAA/BBB unknown to the fixture -> both missing, none documented.
        with pytest.raises(EngineError, match="AAA.*BBB|BBB.*AAA") as exc:
            compute_index("AS-100", ["AAA", "BBB"], records, exclusions=load_exclusions())
        assert "surprises need reconciliation" in str(exc.value)


class TestDeterminism:
    def test_double_run_is_byte_identical(self, computed, tmp_path: Path) -> None:
        benchmarks = _benchmarks_for(computed, _records_for_benchmarks())
        first = write_outputs(tmp_path / "a", computed, benchmarks, "fixture")
        second = write_outputs(tmp_path / "b", computed, benchmarks, "fixture")
        assert first == second
        for name in first:
            assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()

    def test_levels_csv_shape(self, computed) -> None:
        text = levels_csv(computed)
        lines = text.strip().splitlines()
        assert lines[0] == "index_id,date,level,constituents"
        assert "AS-100,2026-11-09,1000.000000,100" in lines
        assert "AS-MCG100,2026-11-13,1000.000000,100" in lines
        assert "AS-MCB100,2026-11-13,1000.000000,100" in lines

    def test_metadata_carries_proxy_mapping_and_attestation(self, computed) -> None:
        payload = json.loads(metadata_json(computed, "fixture"))
        assert payload["benchmark_proxy_mapping"]["SPY"] == "S&P 500 (ETF proxy)"
        assert payload["methodology_version"] == "4.0"
        assert payload["benchmark_sets"]["AS-100"] == ["SPY", "QQQ", "RSP"]
        assert payload["benchmark_sets"]["AS-MCG100"] == ["SPY", "IWM", "IWC"]
        assert payload["benchmark_sets"]["AS-MCB100"] == ["SPY", "IJH", "IWM"]
        assert len(payload["sha256_index_levels_csv"]) == 64
        mc = next(i for i in payload["indices"] if i["index_id"] == "AS-100")
        assert mc["constituent_count"] == 100
        assert mc["excluded"] == []


class TestComparison:
    def test_comparison_exports_render_offline(self, computed, tmp_path: Path) -> None:
        benchmarks = _benchmarks_for(computed, _records_for_benchmarks())
        hashes = write_outputs(tmp_path, computed, benchmarks, "fixture")
        comparison = json.loads((tmp_path / "comparison.json").read_text())
        assert set(comparison["series"]) == {"AS-100", "AS-MCG100", "AS-MCB100"}
        as100 = comparison["series"]["AS-100"]
        assert as100["levels"]["2026-11-09"] == "1000.000000"
        assert set(as100["benchmarks"]) == {"SPY", "QQQ", "RSP"}
        assert as100["benchmarks"]["SPY"]["levels"]["2026-11-09"] == "1000.000000"
        mcb = comparison["series"]["AS-MCB100"]
        assert set(mcb["benchmarks"]) == {"SPY", "IJH", "IWM"}
        assert "comparison.csv" in hashes


def _records_for_benchmarks():
    return load_fixture_records(FIXTURE_CSV)


def _benchmarks_for(computed, records) -> dict[str, list[BenchmarkSeries]]:
    return {
        index.index_id: [
            rebase_proxy(proxy, records.get(proxy, []), index.base_date)
            for proxy in INDEX_BENCHMARKS[index.index_id]
        ]
        for index in computed
    }
