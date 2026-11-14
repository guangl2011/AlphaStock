"""End-to-end correlation pipeline tests over the checked-in fixtures.

Proves the determinism contract (two runs -> byte-identical outputs whose
SHA-256 matches the metadata attestation) and that the serialized outputs
carry the spec'd shapes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pipelines.correlation.__main__ import main

OUTPUT_FILES = (
    "factor_exposures.csv",
    "stock_factor_summary.csv",
    "correlation_matrices.csv",
    "correlation_metadata.json",
)


@pytest.fixture(scope="module")
def run_one(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("corr_run1")
    assert main(["--mode", "fixture", "--out-dir", str(out)]) == 0
    return out


def test_two_runs_are_byte_identical(tmp_path: Path, run_one: Path) -> None:
    out2 = tmp_path / "run2"
    assert main(["--mode", "fixture", "--out-dir", str(out2)]) == 0
    for name in OUTPUT_FILES:
        assert (out2 / name).read_bytes() == (run_one / name).read_bytes(), name


def test_metadata_attestation_matches_file_bytes(run_one: Path) -> None:
    metadata = json.loads((run_one / "correlation_metadata.json").read_text())
    for name, key in (
        ("factor_exposures.csv", "sha256_factor_exposures_csv"),
        ("stock_factor_summary.csv", "sha256_stock_factor_summary_csv"),
        ("correlation_matrices.csv", "sha256_correlation_matrices_csv"),
    ):
        digest = hashlib.sha256((run_one / name).read_bytes()).hexdigest()
        assert metadata[key] == digest, name
    assert metadata["source"] == "fixture"
    assert set(metadata["factor_definitions"]) == {
        "MKT", "SIZE", "MICRO", "BREADTH", "UTIL", "NUKE", "GRID", "ROBO"
    }


def test_factor_exposures_covers_all_three_indices_and_all_factors(run_one: Path) -> None:
    lines = (run_one / "factor_exposures.csv").read_text().splitlines()
    header = lines[0].split(",")
    assert header == [
        "index_id", "factor", "beta", "std_error", "t_stat", "r_squared",
        "corr_full", "corr_63d", "corr_21d", "dropped_collinear", "regression_days",
    ]
    rows = {tuple(line.split(",")[:2]) for line in lines[1:]}
    for index_id in ("AS-100", "AS-MCG100", "AS-MCB100"):
        for factor in ("MKT", "SIZE", "MICRO", "BREADTH", "UTIL", "NUKE", "GRID", "ROBO"):
            assert (index_id, factor) in rows


def test_regression_factors_have_statistics_and_correlation_only_do_not(
    run_one: Path,
) -> None:
    metadata = json.loads((run_one / "correlation_metadata.json").read_text())
    regression = set(metadata["regression_factors"])
    for line in (run_one / "factor_exposures.csv").read_text().splitlines()[1:]:
        (
            index_id, factor, beta, _se, t_stat, _r2,
            corr_full, _63, _21, _drop, _days,
        ) = line.split(",")
        if factor in regression:
            assert beta != "" and t_stat != "", (index_id, factor)
        else:
            assert beta == "" and t_stat == "", (index_id, factor)
        assert corr_full != "", (index_id, factor)


def test_stock_summary_carries_event_rows(run_one: Path) -> None:
    lines = (run_one / "stock_factor_summary.csv").read_text().splitlines()
    event_rows = [line for line in lines[1:] if ",event_" in line]
    assert event_rows, "fixture signals should produce event aggregate rows"
    # Hand-checked fixture fact: AAOI is covered by two positive signals
    # (SIG-2026-0915-A, SIG-2026-1110-E).
    aaoi_events = [line for line in event_rows if line.startswith("AAOI,event_positive")]
    assert len(aaoi_events) == 1
    assert aaoi_events[0].endswith(",2")


def test_matrices_come_in_three_windows(run_one: Path) -> None:
    lines = (run_one / "correlation_matrices.csv").read_text().splitlines()[1:]
    windows = {line.split(",")[2] for line in lines}
    assert windows == {"full", "63d", "21d"}
    # Self-consistency: the matrix never contains a row for a series with itself.
    assert all(line.split(",")[0] != line.split(",")[1] for line in lines)


def test_missing_index_levels_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(Exception, match="index levels file not found"):
        main(
            [
                "--mode", "fixture",
                "--index-levels", str(tmp_path / "absent.csv"),
                "--out-dir", str(tmp_path),
            ]
        )
