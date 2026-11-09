"""Source-health module tests: freshness, failures, loud assertion."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pipelines.ingest.source_health import (
    SourceHealthError,
    SourceHealthRecord,
    assert_healthy,
    evaluate_freshness,
    record_failure,
)

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def test_freshness_ok_within_budget() -> None:
    record = evaluate_freshness("edgar-fts", datetime(2026, 11, 5, tzinfo=UTC), NOW, max_age_days=7)
    assert record.status == "ok"


def test_freshness_stale_beyond_budget() -> None:
    record = evaluate_freshness("edgar-fts", datetime(2026, 10, 1, tzinfo=UTC), NOW, max_age_days=7)
    assert record.status == "stale"


def test_invalid_status_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="status"):
        SourceHealthRecord(source_name="x", status="weird", detail="d")
    with pytest.raises(ValueError, match="detail"):
        SourceHealthRecord(source_name="x", status="ok", detail="")


def test_assert_healthy_raises_on_failed_and_ignores_stale() -> None:
    ok = evaluate_freshness("edgar-fts", NOW, NOW)
    failed = record_failure("county-watchlist", "boom")
    with pytest.raises(SourceHealthError, match="county-watchlist: boom"):
        assert_healthy([ok, failed])

    stale = evaluate_freshness("slow-source", datetime(2026, 10, 1, tzinfo=UTC), NOW)
    assert_healthy([ok, stale])  # stale is a warning status, not a scan failure
