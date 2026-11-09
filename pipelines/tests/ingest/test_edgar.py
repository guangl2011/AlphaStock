"""EDGAR FTS sweep tests — recorded fixtures only, no live EDGAR calls."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from data.universe import load_universe
from pipelines.ingest.edgar import (
    EVENT_CAPACITY_CHANGE,
    EVENT_NEW_PROJECT,
    SOURCE_TYPE,
    EdgarResponseError,
    classify_filing_event,
    filing_source_url,
    parse_hit,
    signal_id_for,
    strength_for,
    sweep,
)
from pipelines.ingest.source_health import SourceHealthError, assert_healthy

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def _fts_payload(hits: list[dict[str, object]]) -> dict[str, object]:
    return {"hits": {"total": {"value": len(hits)}, "hits": hits}}


def _hit(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "_id": "0001193125-26-123456:dform8k.htm",
        "_excerpt": "expansion of our data center campus in Northern Virginia",
        "_source": {
            "cik": "0001297996",
            "display_names": ["DIGITAL REALTY TRUST, INC."],
            "file_type": "8-K",
            "file_date": "2026-11-03",
        },
    }
    base.update(overrides)
    return base


def test_parse_hit_builds_sec_source_url() -> None:
    hit = parse_hit(_hit(), keyword="data center")
    assert hit.url == (
        "https://www.sec.gov/Archives/edgar/data/1297996/000119312526123456/dform8k.htm"
    )
    assert hit.company == "DIGITAL REALTY TRUST, INC."
    assert hit.keyword == "data center"


def test_fixture_filing_maps_to_universe_ticker() -> None:
    universe = load_universe()
    signals, health = sweep([("data center", _fts_payload([_hit()]))], universe, NOW)

    assert_healthy(health)
    assert len(signals) == 1
    signal = signals[0]
    assert signal["category"] == "V4_DATA_CENTER_BUILDOUT"
    assert signal["event_type"] == EVENT_NEW_PROJECT
    assert signal["tickers"] == ["DLR"]  # normalized-name fallback match (case differs)
    assert signal["theme"] == "Data Centers"
    assert signal["summary_en"] and signal["summary_zh"]
    assert signal["invalidation"]
    assert signal["direction"] == "positive"
    assert signal["strength"] == 2
    source = signal["sources"][0]  # type: ignore[index]
    assert source["type"] == SOURCE_TYPE
    assert source["url"].startswith("https://www.sec.gov/Archives/edgar/data/")
    assert signal["verified"] is False


def test_capacity_figure_yields_capacity_change_and_strength() -> None:
    hit = parse_hit(
        _hit(
            _excerpt="added 250 MW of power capacity across two campuses",
        ),
        keyword="power capacity",
    )
    assert classify_filing_event(hit) == EVENT_CAPACITY_CHANGE
    assert strength_for(hit) == 4
    assert hit.capacity_mw == 250.0


def test_small_capacity_and_gw_normalization() -> None:
    # Any explicit capacity figure → capacity_change per the module contract;
    # strength grades by size (>=100 MW → 4, otherwise 3).
    small = parse_hit(_hit(_excerpt="an initial 40 MW deployment"), keyword="power capacity")
    assert classify_filing_event(small) == EVENT_CAPACITY_CHANGE
    assert strength_for(small) == 3
    gw = parse_hit(_hit(_excerpt="a 1.2 GW energy expansion"), keyword="energy expansion")
    assert gw.capacity_mw == pytest.approx(1200.0)
    assert strength_for(gw) == 4


def test_signal_id_is_content_addressed_and_idempotent() -> None:
    universe = load_universe()
    first, health_a = sweep([("data center", _fts_payload([_hit()]))], universe, NOW)
    second, health_b = sweep([("data center", _fts_payload([_hit()]))], universe, NOW)

    assert first[0]["signal_id"] == second[0]["signal_id"]  # type: ignore[index]
    assert signal_id_for(parse_hit(_hit(), keyword="data center")).startswith("SIG-20261103-")
    assert_healthy(health_a)
    assert_healthy(health_b)


def test_malformed_response_records_failure_not_silent_zero() -> None:
    universe = load_universe()
    signals, health = sweep(
        [("data center", {"unexpected": "shape"})], universe, NOW
    )
    assert signals == []
    assert health[0].status == "failed"  # type: ignore[index]
    with pytest.raises(SourceHealthError):
        assert_healthy(health)


def test_hit_missing_fields_raise_loudly() -> None:
    with pytest.raises(EdgarResponseError):
        parse_hit(_hit(_source={"cik": "1"}), keyword="hyperscale")
    with pytest.raises(EdgarResponseError):
        parse_hit(_hit(_id="no-separator"), keyword="hyperscale")


def test_filing_source_url_format() -> None:
    assert filing_source_url("0001297996", "0001193125-26-123456", "dform8k.htm") == (
        "https://www.sec.gov/Archives/edgar/data/1297996/000119312526123456/dform8k.htm"
    )
