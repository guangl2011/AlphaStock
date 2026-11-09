"""V2 scan-path source-health tests: stale/failed sources go red.

The shared module's lenient path (stale = warning) is PR #5's contract and
stays tested in tests/ingest/test_source_health.py. Here the queue scan's
strictness is verified: stale or failed sources fail the scan loudly, an
empty source is a failure (an ISO queue is never empty), and adapter errors
become failed records instead of silent zero-signal scans.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pipelines.ingest.queues.base import QueueProject
from pipelines.ingest.queues.health import (
    QUEUE_MAX_AGE_DAYS,
    check_queue_source,
    evaluate_queue_source,
    queue_source_name,
)
from pipelines.ingest.source_health import (
    SourceHealthError,
    SourceHealthRecord,
    assert_healthy,
    assert_scan_healthy,
    record_failure,
)

NOW = datetime(2026, 11, 9, 6, 0, tzinfo=UTC)


def project(retrieved_at: datetime = NOW, iso: str = "PJM") -> QueueProject:
    return QueueProject(
        iso=iso,
        project_id="PJM-0001",
        name="Test Project",
        capacity_mw=100.0,
        load_type="unknown",
        data_center_likelihood=0.5,
        status="Studying",
        status_norm="studied",
        county=None,
        state=None,
        raw={},
        source_url="https://example.com/pjm",
        retrieved_at=retrieved_at,
    )


def test_source_name_is_table_friendly() -> None:
    assert queue_source_name("PJM") == "queue-pjm"
    assert queue_source_name("ISO-NE") == "queue-iso-ne"


def test_fresh_source_is_ok() -> None:
    record = evaluate_queue_source("PJM", [project()], NOW)
    assert record.status == "ok"
    assert record.source_name == "queue-pjm"


def test_empty_source_is_a_failure() -> None:
    record = evaluate_queue_source("PJM", [], NOW)
    assert record.status == "failed"
    assert "zero projects" in record.detail


def test_stale_source_names_the_newest_row() -> None:
    old = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)  # 39 days before NOW
    record = evaluate_queue_source("PJM", [project(old)], NOW)
    assert record.status == "stale"
    assert "2026-10-01" in record.detail


def test_freshness_budget_is_weekly() -> None:
    assert QUEUE_MAX_AGE_DAYS == 7
    within = datetime(2026, 11, 4, 6, 0, tzinfo=UTC)  # 5 days before NOW
    assert evaluate_queue_source("PJM", [project(within)], NOW).status == "ok"
    beyond = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)  # 8 days before NOW
    assert evaluate_queue_source("PJM", [project(beyond)], NOW).status == "stale"


def test_adapter_error_becomes_a_failed_record() -> None:
    def boom() -> list[QueueProject]:
        raise RuntimeError("PJM workbook changed shape")

    record = check_queue_source("PJM", boom, NOW)
    assert record.status == "failed"
    assert "fetch failed" in record.detail
    assert "workbook changed shape" in record.detail


def test_check_queue_source_ok_path() -> None:
    record = check_queue_source("PJM", lambda: [project()], NOW)
    assert record.status == "ok"


class TestAssertScanHealthy:
    def test_all_ok_passes(self) -> None:
        # No exception raised = scan passes; the call is the assertion.
        assert_scan_healthy([SourceHealthRecord("queue-pjm", "ok", "fine")])

    def test_failed_record_raises(self) -> None:
        with pytest.raises(SourceHealthError, match="queue-pjm"):
            assert_scan_healthy([record_failure("queue-pjm", "boom")])

    def test_stale_record_also_raises_on_the_scan_path(self) -> None:
        stale = SourceHealthRecord("queue-ercot", "stale", "last success 39d ago")
        with pytest.raises(SourceHealthError, match="queue-ercot \\[stale\\]"):
            assert_scan_healthy([stale])

    def test_message_lists_every_unhealthy_source(self) -> None:
        records = [
            SourceHealthRecord("queue-miso", "ok", "fresh"),
            SourceHealthRecord("queue-pjm", "stale", "old"),
            record_failure("queue-caiso", "boom"),
        ]
        with pytest.raises(SourceHealthError) as excinfo:
            assert_scan_healthy(records)
        message = str(excinfo.value)
        assert "queue-pjm [stale]" in message
        assert "queue-caiso [failed]" in message
        assert "queue-miso" not in message

    def test_lenient_path_still_treats_stale_as_warning(self) -> None:
        # PR #5's publish-path contract is untouched by the scan gate.
        stale = SourceHealthRecord("slow-source", "stale", "old")
        assert assert_healthy([stale]) is None
