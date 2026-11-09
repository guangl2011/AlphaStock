"""County permitting watchlist tests — fixture records only, no live calls."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pipelines.ingest.counties import (
    MAX_COUNTIES,
    MIN_COUNTIES,
    SOURCE_TYPE,
    AnnouncementError,
    WatchlistValidationError,
    ingest_announcements,
    load_watchlist,
    parse_announcements,
)
from pipelines.ingest.source_health import SourceHealthError, assert_healthy

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def _record(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "county": "Loudoun",
        "state": "Virginia",
        "date": "2026-11-05",
        "headline": "Board approves special exception for 96-acre data center campus",
        "detail": "Campus will support an initial 120 MW of critical load.",
        "url": "https://www.loudoun.gov/agenda/2026-11-05-dc-campus",
    }
    base.update(overrides)
    return base


def test_watchlist_loads_with_pilot_band_and_schema() -> None:
    counties = load_watchlist()
    assert MIN_COUNTIES <= len(counties) <= MAX_COUNTIES
    keys = [c.key for c in counties]
    assert len(keys) == len(set(keys))
    assert all(c.source_url.startswith("https://") for c in counties)
    assert all(c.county and c.state and c.region_note for c in counties)
    # Data-center-heavy jurisdictions per the market research doc.
    assert "Loudoun, Virginia" in keys


def test_watchlist_county_band_is_enforced(tmp_path) -> None:
    csv_path = tmp_path / "watchlist.csv"
    header = "county,state,region_note,source_url\n"

    def rows(n: int) -> str:
        return "".join(f"County{i},State{i},note,https://example.com/{i}\n" for i in range(n))

    csv_path.write_text(header + rows(MIN_COUNTIES - 1), encoding="utf-8")
    with pytest.raises(WatchlistValidationError, match="10-20"):
        load_watchlist(csv_path)

    csv_path.write_text(header + rows(MAX_COUNTIES + 1), encoding="utf-8")
    with pytest.raises(WatchlistValidationError, match="10-20"):
        load_watchlist(csv_path)


def test_watchlist_rejects_duplicates_and_empty_fields(tmp_path) -> None:
    csv_path = tmp_path / "watchlist.csv"
    header = "county,state,region_note,source_url\n"
    dup = "Loudoun,Virginia,note,https://www.loudoun.gov/\n"

    csv_path.write_text(header + dup * 10, encoding="utf-8")
    with pytest.raises(WatchlistValidationError, match="duplicate"):
        load_watchlist(csv_path)

    csv_path.write_text(
        header + "Loudoun,Virginia,,https://www.loudoun.gov/\n" + dup * 9, encoding="utf-8"
    )
    with pytest.raises(WatchlistValidationError, match="empty field"):
        load_watchlist(csv_path)


def test_watchlist_missing_file_raises(tmp_path) -> None:
    with pytest.raises(WatchlistValidationError, match="not found"):
        load_watchlist(tmp_path / "nope.csv")


def test_watchlist_announcement_maps_to_canonical_event() -> None:
    watchlist = load_watchlist()
    signals, health = ingest_announcements([_record()], watchlist, NOW)

    assert_healthy(health)
    assert len(signals) == 1
    signal = signals[0]
    assert signal["category"] == "V4_DATA_CENTER_BUILDOUT"
    assert signal["event_type"] == "new_project"
    assert signal["theme"] == "Data Centers"
    assert signal["tickers"] == []  # theme-level signal; mapping table owns ticker judgment
    assert signal["strength"] == 4  # 120 MW >= 100 MW bulk threshold
    assert signal["summary_en"] and signal["summary_zh"]
    assert signal["invalidation"]
    source = signal["sources"][0]  # type: ignore[index]
    assert source["type"] == SOURCE_TYPE
    assert source["url"] == _record()["url"]
    assert signal["verified"] is False


def test_non_watchlist_county_is_config_skipped_not_failed() -> None:
    watchlist = load_watchlist()
    signals, health = ingest_announcements(
        [_record(county="Unmonitored", state="Nevada")], watchlist, NOW
    )
    assert signals == []
    assert health[0].status == "ok"  # type: ignore[index]
    assert "1 non-watchlist" in health[0].detail  # type: ignore[index]
    assert_healthy(health)


def test_malformed_announcement_is_a_failure_not_silent_zero() -> None:
    watchlist = load_watchlist()
    signals, health = ingest_announcements([_record(url="")], watchlist, NOW)
    assert signals == []
    assert health[0].status == "failed"  # type: ignore[index]
    with pytest.raises(SourceHealthError):
        assert_healthy(health)

    # The parser itself raises loudly; the batch path converts to a failed record.
    with pytest.raises(AnnouncementError):
        parse_announcements([{**_record(), "headline": ""}])


def test_ingest_is_idempotent_on_identical_records() -> None:
    watchlist = load_watchlist()
    first, _ = ingest_announcements([_record()], watchlist, NOW)
    second, _ = ingest_announcements([_record()], watchlist, NOW)
    assert first[0]["signal_id"] == second[0]["signal_id"]  # type: ignore[index]
    assert first[0]["signal_id"].startswith("SIG-20261105-")  # type: ignore[index]


def test_gw_figures_normalize_to_mw_for_strength() -> None:
    watchlist = load_watchlist()
    signals, _ = ingest_announcements(
        [_record(detail="Phase one totals 0.5 GW of critical IT load.")], watchlist, NOW
    )
    assert signals[0]["strength"] == 4  # type: ignore[index]
