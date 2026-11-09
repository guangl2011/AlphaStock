"""Weekly scan runner tests: the gate, the quiet week, idempotence, snapshot rotation.

The scan composes the merged pipeline pieces; these tests verify the
composition contract — health checks run for all 7 ISOs, stale/failed
sources raise before anything publishes, a healthy quiet week is an honest
zero, and the returned snapshot is next week's valid diff baseline.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipelines.ingest.queues import ALL_ISOS, QueueProject
from pipelines.ingest.source_health import SourceHealthError, SourceHealthRecord, record_failure
from pipelines.scan import (
    load_snapshot_index,
    run_weekly_scan,
    snapshot_payload,
    write_snapshot,
)
from pipelines.signals.queue_diff import index_projects

NOW = datetime(2026, 11, 9, 6, 0, tzinfo=UTC)
FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "queues"


def project(
    iso: str,
    project_id: str,
    status_norm: str = "studied",
    **overrides: object,
) -> QueueProject:
    return QueueProject(
        iso=iso,
        project_id=project_id,
        name=f"Project {project_id}",
        capacity_mw=100.0,
        load_type="unknown",
        data_center_likelihood=0.5,
        status=status_norm,
        status_norm=status_norm,
        county=None,
        state=None,
        raw={},
        source_url=f"https://example.com/{iso.lower()}",
        retrieved_at=NOW,
        **overrides,
    )


def projects_per_iso() -> dict[str, list[QueueProject]]:
    # One project per ISO — all fresh (retrieved this scan), all new vs the
    # empty previous index.
    return {iso: [project(iso, f"{iso}-0001")] for iso in ALL_ISOS}


def watchlist() -> list[object]:
    from pipelines.ingest.counties import load_watchlist

    return load_watchlist()


def universe() -> list[object]:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from data.universe import load_universe

    return load_universe()


def test_scan_reports_all_seven_iso_health_checks() -> None:
    result = run_weekly_scan(
        projects_per_iso(),
        {},
        watchlist=watchlist(),  # type: ignore[arg-type]
        county_records=None,
        edgar_responses=None,
        universe=universe(),  # type: ignore[arg-type]
        now=NOW,
    )
    names = {r.source_name for r in result.health}
    assert {f"queue-{iso.lower()}" for iso in ALL_ISOS} <= names
    assert all(r.status == "ok" for r in result.health if r.source_name.startswith("queue-"))


def test_quiet_week_with_healthy_sources_is_an_honest_zero() -> None:
    # Same snapshot as previous scan → idempotent diff → zero candidates.
    index = index_projects([p for ps in projects_per_iso().values() for p in ps])
    result = run_weekly_scan(
        projects_per_iso(),
        index,
        watchlist=watchlist(),  # type: ignore[arg-type]
        county_records=None,
        edgar_responses=None,
        universe=universe(),  # type: ignore[arg-type]
        now=NOW,
    )
    assert result.signals == []
    assert all(r.status == "ok" for r in result.health)


def test_failed_prefetch_source_raises_before_publish() -> None:
    published: list[object] = []

    class RecordingConnection:
        def execute(self, sql: str, params: object = None) -> None:
            published.append((sql, params))

        def commit(self) -> None:
            published.append("commit")

        def rollback(self) -> None:
            published.append("rollback")

    failing = record_failure("queue-spp", "live fetch failed: HTTP 503")
    with pytest.raises(SourceHealthError):
        run_weekly_scan(
            projects_per_iso(),
            {},
            watchlist=watchlist(),  # type: ignore[arg-type]
            county_records=None,
            edgar_responses=None,
            universe=universe(),  # type: ignore[arg-type]
            now=NOW,
            conn=RecordingConnection(),  # type: ignore[arg-type]
            prefetch_health=[failing],
        )
    # Nothing was written to the store: the gate fires before any statement.
    assert published == []


def test_scan_emits_diff_events_and_a_rotatable_snapshot(tmp_path: Path) -> None:
    # The baseline is a test-owned fixture (crafted from the golden fixtures
    # with three modifications) — NOT the runtime-rotated snapshot, which the
    # weekly workflow overwrites on main (that rotation broke this test once
    # already: the committed baseline matched the pristine fixtures and the
    # diff came back empty).
    prev = load_snapshot_index(
        Path(__file__).resolve().parents[1] / "fixtures" / "snapshots" / "queue_baseline.json"
    )
    # Current scan = the pristine golden fixtures (the baseline was crafted
    # from them with three modifications).
    current: dict[str, list[QueueProject]] = {}
    for iso in ALL_ISOS:
        path = FIXTURE_DIR / f"{iso.lower().replace('-', '')}_snapshot.json"
        from pipelines.ingest.queues import build_adapter

        current[iso] = build_adapter(
            iso, lambda p=path: json.loads(p.read_text(encoding="utf-8")), NOW
        ).fetch()

    result = run_weekly_scan(
        current,
        prev,
        watchlist=watchlist(),  # type: ignore[arg-type]
        county_records=None,
        edgar_responses=None,
        universe=universe(),  # type: ignore[arg-type]
        now=NOW,
    )
    event_types = {s["event_type"] for s in result.signals}
    # The baseline was crafted to yield one of each: forward transition
    # (PJM-0001), withdrawal (ERCOT INR-100251), capacity change (MISO M-44812).
    assert "status_transition" in event_types
    assert "withdrawal" in event_types
    assert "capacity_change" in event_types

    # The snapshot rotates: writing it and loading it round-trips exactly.
    out = write_snapshot(result.snapshot_index, tmp_path / "snap" / "queue_snapshot.json")
    reloaded = load_snapshot_index(out)
    assert snapshot_payload(reloaded) == snapshot_payload(result.snapshot_index)
    # And diffing the reloaded baseline against itself is idempotent.
    rerun = run_weekly_scan(
        current,
        reloaded,
        watchlist=watchlist(),  # type: ignore[arg-type]
        county_records=None,
        edgar_responses=None,
        universe=universe(),  # type: ignore[arg-type]
        now=NOW,
    )
    assert rerun.signals == []


def test_scan_stamps_published_at_and_carries_v4_legs() -> None:
    from pipelines.ingest.counties import ingest_announcements

    announcements = [
        {
            "county": "Loudoun",
            "state": "Virginia",
            "date": "2026-11-03",
            "headline": "Campus special exception filed",
            "detail": "A 250 MW campus special exception advanced.",
            "url": "https://permits/loudoun-scan",
        }
    ]
    county_signals, county_health = ingest_announcements(
        announcements,
        watchlist(),
        NOW,  # type: ignore[arg-type]
    )
    result = run_weekly_scan(
        projects_per_iso(),
        {},
        watchlist=watchlist(),  # type: ignore[arg-type]
        county_records=announcements,
        edgar_responses=None,
        universe=universe(),  # type: ignore[arg-type]
        now=NOW,
    )
    assert county_health[0].status == "ok"  # type: ignore[index]
    assert result.signals
    for signal in result.signals:
        assert signal["published_at"] == "2026-11-09T06:00:00Z"
    county_in_result = [
        s for s in result.signals if any(src["type"] == "COUNTY_PERMIT" for src in s["sources"])
    ]
    assert len(county_in_result) == 1
    assert county_signals[0]["signal_id"] == county_in_result[0]["signal_id"]


def test_health_record_rejects_invalid_status() -> None:
    with pytest.raises(ValueError):
        SourceHealthRecord("queue-pjm", "perfect", "not a status")
    with pytest.raises(ValueError):
        SourceHealthRecord("queue-pjm", "ok", "")
