"""Queue-diff tests: idempotence, event coverage, canonical row contract.

Headline case (build spec acceptance criterion): diffing a snapshot against
itself yields ZERO candidates. Everything else verifies one event type per
diff and the provenance fields each row carries.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pipelines.ingest.queues.base import QueueProject
from pipelines.signals.queue_diff import (
    diff_projects,
    index_projects,
    project_key,
    signal_id_for,
)
from pipelines.signals.transitions import (
    EVENT_CAPACITY_CHANGE,
    EVENT_NEW_PROJECT,
    EVENT_STATUS_TRANSITION,
    EVENT_WITHDRAWAL,
    classify_pair,
)

PREV_AT = datetime(2026, 11, 2, 6, 0, tzinfo=UTC)
CURR_AT = datetime(2026, 11, 9, 6, 0, tzinfo=UTC)


def project(
    status: str = "studied",
    capacity: float | None = 100.0,
    pid: str = "PJM-0001",
    iso: str = "PJM",
    load_type: str = "unknown",
    retrieved_at: datetime = CURR_AT,
) -> QueueProject:
    return QueueProject(
        iso=iso,
        project_id=pid,
        name=f"Project {pid}",
        capacity_mw=capacity,
        load_type=load_type,
        data_center_likelihood=0.5,
        status=status,
        status_norm=status,
        county=None,
        state=None,
        raw={},
        source_url=f"https://example.com/{iso.lower()}-queue",
        retrieved_at=retrieved_at,
    )


def test_identical_snapshots_yield_zero_candidates() -> None:
    snapshot = [
        project("requested"),
        project("studied", pid="PJM-0002"),
        project("energized", pid="MISO-7", iso="MISO", capacity=None),
    ]
    prev = index_projects(snapshot)
    assert diff_projects(prev, snapshot) == []


def test_repeated_diff_is_a_pure_recomputation() -> None:
    prev = index_projects([project("studied", 150.0)])
    curr = [project("esa_signed", 150.0)]
    first = diff_projects(prev, curr)
    second = diff_projects(prev, curr)
    assert first == second
    assert first  # the pair did change — rows exist, and re-running them
    # through the store dedupes by signal_id (idempotent ingest)


def test_new_project_event() -> None:
    rows = diff_projects({}, [project("requested")])
    assert len(rows) == 1
    assert rows[0]["event_type"] == EVENT_NEW_PROJECT
    assert rows[0]["status_to"] == "requested"
    assert rows[0]["status_from"] is None


def test_withdrawal_event() -> None:
    prev = index_projects([project("studied", retrieved_at=PREV_AT)])
    rows = diff_projects(prev, [])
    assert len(rows) == 1
    assert rows[0]["event_type"] == EVENT_WITHDRAWAL
    assert rows[0]["direction"] == "negative"
    assert rows[0]["strength"] == 3
    # A withdrawal is observed by the scan that last SAW the project.
    source = rows[0]["sources"][0]
    assert source["retrieved_at"] == "2026-11-02T06:00:00Z"


def test_status_transition_event() -> None:
    prev = index_projects([project("studied")])
    rows = diff_projects(prev, [project("approved")])
    assert len(rows) == 1
    assert rows[0]["event_type"] == EVENT_STATUS_TRANSITION
    assert (rows[0]["status_from"], rows[0]["status_to"]) == ("studied", "approved")


def test_backward_move_is_negative() -> None:
    prev = index_projects([project("approved")])
    rows = diff_projects(prev, [project("studied")])
    assert rows[0]["direction"] == "negative"


def test_capacity_change_is_a_context_event() -> None:
    prev = index_projects([project("studied", 150.0)])
    rows = diff_projects(prev, [project("studied", 300.0)])
    assert len(rows) == 1
    assert rows[0]["event_type"] == EVENT_CAPACITY_CHANGE
    assert rows[0]["strength"] == 1


def test_mixed_diff_covers_every_event_type_once() -> None:
    prev = index_projects(
        [
            project("studied", pid="PJM-0001"),          # → approved
            project("energized", pid="PJM-0002"),        # capacity doubles
            project("esa_signed", pid="PJM-0003"),       # vanishes → withdrawal
            project("requested", pid="PJM-0004"),        # unchanged
        ]
    )
    curr = [
        project("approved", pid="PJM-0001"),
        project("energized", 200.0, pid="PJM-0002"),
        project("requested", pid="PJM-0005"),            # new
        project("requested", pid="PJM-0004"),
    ]
    rows = diff_projects(prev, curr)
    by_type = sorted(str(row["event_type"]) for row in rows)
    assert by_type == [
        EVENT_CAPACITY_CHANGE,
        EVENT_NEW_PROJECT,
        EVENT_STATUS_TRANSITION,
        EVENT_WITHDRAWAL,
    ]


def test_rows_sorted_by_signal_id() -> None:
    prev = index_projects([project("studied", pid="PJM-0001")])
    curr = [project("approved", pid="PJM-0001"), project("requested", pid="PJM-0002")]
    ids = [str(row["signal_id"]) for row in diff_projects(prev, curr)]
    assert ids == sorted(ids)


def test_signal_id_is_content_addressed() -> None:
    event = classify_pair(project("studied"), project("approved"))
    first = signal_id_for(event, retrieved_at="2026-11-09T06:00:00Z")
    second = signal_id_for(
        classify_pair(project("studied"), project("approved")),
        retrieved_at="2026-11-09T06:00:00Z",
    )
    assert first.startswith("SIG-20261109-")
    assert len(first.split("-")[-1]) == 8  # sha256 prefix
    assert first == second  # same event, same id — idempotent ingest


def test_signal_id_changes_with_observing_snapshot() -> None:
    event = classify_pair(project("studied"), project("approved"))
    early = signal_id_for(event, "2026-11-02T06:00:00Z")
    late = signal_id_for(event, "2026-11-09T06:00:00Z")
    assert early != late  # a later scan is a new, honestly-timestamped event


def test_theme_follows_load_type() -> None:
    for load_type, theme in (
        ("generation", "AI Power"),
        ("large_load", "Data Centers"),
        ("mixed", "Grid & Transmission"),
        ("unknown", "Grid & Transmission"),
    ):
        rows = diff_projects({}, [project(load_type=load_type)])
        assert rows[0]["theme"] == theme


def test_rows_carry_source_provenance() -> None:
    rows = diff_projects({}, [project("requested", iso="ERCOT")])
    source = rows[0]["sources"][0]
    assert source["type"] == "ISO_queue"
    assert source["url"] == "https://example.com/ercot-queue"
    assert source["retrieved_at"] == "2026-11-09T06:00:00Z"
    assert rows[0]["category"] == "V2_INTERCONNECTION"


def test_rows_are_bilingual() -> None:
    rows = diff_projects({}, [project("requested")])
    assert rows[0]["summary_en"].strip()
    assert "并网队列" in rows[0]["summary_zh"]


def test_project_key_is_isolated_per_iso() -> None:
    assert project_key("PJM", "1") != project_key("MISO", "1")
