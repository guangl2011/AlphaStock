"""End-to-end pipeline tests on the checked-in fixture closes.

The fixture (tests/fixtures/index/base_closes.csv) carries the recorded
Tiingo base-date closes for all 199 resolvable constituents — the same
numbers as the Tiingo Coverage sheet (sh_3qRWhewT). These tests prove the
DoD anchor: AS-100 = exactly 1000.0 on 2026-11-06 and AS-MC100 = exactly
1000.0 on 2026-11-09, reproducibly, offline.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

import pytest

from pipelines.indices.__main__ import load_fixture_records
from pipelines.indices.benchmarks import INDEX_BENCHMARKS, BenchmarkSeries, rebase_proxy
from pipelines.indices.compute import (
    BASE_DATES,
    compute_index,
    levels_csv,
    load_compositions,
    metadata_json,
    write_outputs,
)
from pipelines.indices.exclusions import load_exclusions
from pipelines.indices.replacements import load_replacements

FIXTURE_CSV = Path(__file__).resolve().parents[1] / "fixtures" / "index" / "base_closes.csv"

AS100_BASE = date(2026, 11, 6)
ASMC100_BASE = date(2026, 11, 9)


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
        for index_id in ("AS-100", "AS-MC100")
    ]


class TestBaseDates:
    def test_as100_is_exactly_1000_on_2026_11_06(self, computed) -> None:
        as100 = next(i for i in computed if i.index_id == "AS-100")
        assert as100.levels[AS100_BASE] == 1000.0

    def test_asmc100_is_exactly_1000_on_2026_11_09(self, computed) -> None:
        mc = next(i for i in computed if i.index_id == "AS-MC100")
        assert mc.levels[ASMC100_BASE] == 1000.0

    def test_computed_from_recorded_fixture_closes_offline(self, records) -> None:
        """The anchor is reproducible from stored closes — no network, no API key."""
        assert records  # fixture loaded
        assert BASE_DATES["AS-100"] == AS100_BASE
        assert BASE_DATES["AS-MC100"] == ASMC100_BASE


class TestPSTGExclusion:
    def test_pstg_excluded_loudly_and_index_computes_over_99(
        self, computed, caplog: pytest.LogCaptureFixture
    ) -> None:
        mc = next(i for i in computed if i.index_id == "AS-MC100")
        with caplog.at_level(logging.WARNING):
            # compute_index already ran in the fixture; recompute to capture logs.
            exclusions = load_exclusions()
            recomputed = compute_index(
                "AS-MC100", load_compositions()["AS-MC100"],
                load_fixture_records(FIXTURE_CSV), exclusions=exclusions,
            )
        assert "PSTG" in caplog.text
        assert "EXCLUSION" in caplog.text
        assert "Tiingo price history" in caplog.text
        assert len(recomputed.constituents) == 99
        assert "PSTG" not in recomputed.constituents
        assert mc.levels[ASMC100_BASE] == 1000.0
        # The exclusion is recorded in the computed index too.
        assert [e.ticker for e in mc.excluded] == ["PSTG"]

    def test_pstg_still_in_universe_data(self) -> None:
        """The constituent CSV keeps all 100 members; exclusion is at computation
        time, documented in index_exclusions.csv — never a silent data edit."""
        from data.large_cap import load_large_cap

        entries = load_large_cap()
        assert len(entries) == 100
        assert any(e.ticker == "PSTG" for e in entries)
        exclusions = load_exclusions()
        assert [e.ticker for e in exclusions] == ["PSTG"]
        assert exclusions[0].universe == "LargeCap100"

    def test_undocumented_gap_fails_the_run(self, records) -> None:
        """A missing constituent with no documented exclusion never computes quietly."""
        from pipelines.indices.engine import EngineError

        # AAA/BBB unknown to the fixture -> both missing, none documented.
        with pytest.raises(EngineError, match="AAA.*BBB|BBB.*AAA") as exc:
            compute_index("AS-MC100", ["AAA", "BBB"], records, exclusions=load_exclusions())
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
        as100_base = [ln for ln in lines if ln.startswith("AS-100,2026-11-06")]
        assert as100_base == ["AS-100,2026-11-06,1000.000000,100"]
        mc_base = [ln for ln in lines if ln.startswith("AS-MC100,2026-11-09")]
        assert mc_base == ["AS-MC100,2026-11-09,1000.000000,99"]

    def test_metadata_carries_proxy_mapping_and_attestation(self, computed) -> None:
        payload = json.loads(metadata_json(computed, "fixture"))
        assert payload["benchmark_proxy_mapping"]["SPY"] == "S&P 500 (ETF proxy)"
        assert payload["benchmark_sets"]["AS-100"] == ["SPY", "IWM", "IWC"]
        assert payload["benchmark_sets"]["AS-MC100"] == ["SPY", "QQQ", "RSP"]
        assert len(payload["sha256_index_levels_csv"]) == 64
        mc = next(i for i in payload["indices"] if i["index_id"] == "AS-MC100")
        assert mc["constituent_count"] == 99
        assert mc["excluded"] == [{
            "ticker": "PSTG",
            "reason": mc["excluded"][0]["reason"],
            "first_observed": "2026-11-10",
            "source": "Tiingo EOD API, checked 2026-11-10",
        }]
        assert "Tiingo EOD gap" in mc["excluded"][0]["reason"]


class TestComparison:
    def test_comparison_exports_render_offline(self, computed, tmp_path: Path) -> None:
        benchmarks = _benchmarks_for(computed, _records_for_benchmarks())
        hashes = write_outputs(tmp_path, computed, benchmarks, "fixture")
        comparison = json.loads((tmp_path / "comparison.json").read_text())
        assert set(comparison["series"]) == {"AS-100", "AS-MC100"}
        as100 = comparison["series"]["AS-100"]
        assert as100["levels"]["2026-11-06"] == "1000.000000"
        assert set(as100["benchmarks"]) == {"SPY", "IWM", "IWC"}
        assert as100["benchmarks"]["SPY"]["levels"]["2026-11-06"] == "1000.000000"
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
