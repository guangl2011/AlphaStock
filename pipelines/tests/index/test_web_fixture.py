"""Tests for the Explorer web-fixture export (pipelines.indices.web_fixture)."""

from __future__ import annotations

import json

import pytest

from pipelines.indices.web_fixture import build_fixture


def _comparison() -> dict:
    return {
        "benchmark_proxy_mapping": {
            "IWC": "Russell Microcap (ETF proxy)",
            "IWM": "Russell 2000 (ETF proxy)",
            "QQQ": "Nasdaq-100 (ETF proxy)",
            "RSP": "S&P 500 Equal Weight (ETF proxy)",
            "SPY": "S&P 500 (ETF proxy)",
            "URA": "Uranium (thematic comparator, ETF proxy)",  # not displayed
        },
        "series": {
            "AS-100": {
                "levels": {"2026-11-06": "1000.000000", "2026-11-09": "1055.503888"},
                "benchmarks": {
                    "IWC": {
                        "name": "Russell Microcap (ETF proxy)",
                        "levels": {"2026-11-06": "1000.000000", "2026-11-09": "1015.384615"},
                    },
                    "IWM": {
                        "name": "Russell 2000 (ETF proxy)",
                        "levels": {"2026-11-06": "1000.000000", "2026-11-09": "991.666667"},
                    },
                    "SPY": {
                        "name": "S&P 500 (ETF proxy)",
                        "levels": {"2026-11-06": "1000.000000", "2026-11-09": "1010.000000"},
                    },
                },
                "date_gaps": [],
            },
            "AS-MC100": {
                "levels": {"2026-11-09": "1000.000000", "2026-11-10": "998.123456"},
                "benchmarks": {
                    "QQQ": {
                        "name": "Nasdaq-100 (ETF proxy)",
                        "levels": {"2026-11-09": "1000.000000", "2026-11-10": "1004.000000"},
                    },
                    "RSP": {
                        "name": "S&P 500 Equal Weight (ETF proxy)",
                        "levels": {"2026-11-09": "1000.000000", "2026-11-10": "999.000000"},
                    },
                    "SPY": {
                        "name": "S&P 500 (ETF proxy)",
                        "levels": {"2026-11-09": "1010.000000", "2026-11-10": "1012.000000"},
                    },
                },
                "date_gaps": [],
            },
        },
    }


def _metadata() -> dict:
    return {
        "methodology_version": "3.0",
        "source": "fixture",
        "indices": [
            {
                "index_id": "AS-100",
                "base_date": "2026-11-06",
                "constituent_count": 100,
                "excluded": [],
            },
            {
                "index_id": "AS-MC100",
                "base_date": "2026-11-09",
                "constituent_count": 99,
                "excluded": [
                    {"ticker": "PSTG", "reason": "Tiingo EOD gap: no price history available"}
                ],
            },
        ],
    }


def test_build_fixture_merges_comparison_and_metadata():
    fixture = build_fixture(_comparison(), _metadata())

    assert fixture["methodology_version"] == "3.0"
    assert fixture["source"] == "fixture"
    assert set(fixture["indices"]) == {"AS-100", "AS-MC100"}

    mc = fixture["indices"]["AS-MC100"]
    assert mc["base_date"] == "2026-11-09"
    assert mc["constituent_count"] == 99
    # Levels survive the merge as the pipeline serialized them.
    assert mc["levels"]["2026-11-10"] == "998.123456"
    # Exclusions are carried so the UI can footnote them honestly.
    assert mc["excluded"] == [
        {"ticker": "PSTG", "reason": "Tiingo EOD gap: no price history available"}
    ]
    # AS-100 has no documented exclusions.
    assert fixture["indices"]["AS-100"]["excluded"] == []


def test_build_fixture_trims_proxies_to_displayed_benchmark_sets():
    fixture = build_fixture(_comparison(), _metadata())

    # AS-100 compares against SPY/IWM/IWC; AS-MC100 against SPY/QQQ/RSP —
    # the thematic comparator (URA) must not leak into the web payload.
    assert set(fixture["benchmark_proxy_mapping"]) == {"IWC", "IWM", "QQQ", "RSP", "SPY"}
    assert set(fixture["indices"]["AS-100"]["benchmarks"]) == {"IWC", "IWM", "SPY"}
    assert set(fixture["indices"]["AS-MC100"]["benchmarks"]) == {"QQQ", "RSP", "SPY"}


def test_build_fixture_is_deterministic():
    first = build_fixture(_comparison(), _metadata())
    second = build_fixture(_comparison(), _metadata())
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_build_fixture_fails_loudly_on_index_id_disagreement():
    comparison = _comparison()
    comparison["series"]["AS-GHOST"] = comparison["series"]["AS-100"]
    with pytest.raises(ValueError, match="index ids disagree"):
        build_fixture(comparison, _metadata())


def test_build_fixture_fails_loudly_on_missing_benchmark():
    comparison = _comparison()
    del comparison["series"]["AS-100"]["benchmarks"]["SPY"]
    with pytest.raises(ValueError, match="missing benchmarks \\['SPY'\\]"):
        build_fixture(comparison, _metadata())
