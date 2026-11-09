"""Queue-source health: per-ISO checks that turn silent data rot red.

The scan's contract (build spec acceptance criteria): a stale or failed
source marks the scan failed — the workflow goes red — and never silently
produces zero signals. These checks wrap the shared source-health records
with queue-specific policy:

* an ISO queue is never empty — zero rows means a format change, not a quiet
  week, so it fails;
* freshness runs against the NEWEST row's ``retrieved_at`` (queue sites
  publish full snapshots; one fresh row means the source answered).

The scan runner composes: ``check_queue_source`` per ISO → persist the
records to ``source_health`` → ``assert_scan_healthy`` — a raised
``SourceHealthError`` is what turns the workflow red.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime

from pipelines.ingest.queues.base import QueueProject
from pipelines.ingest.source_health import (
    SourceHealthRecord,
    evaluate_freshness,
    record_failure,
)

#: Weekly scan cadence: a source older than a week missed its budget.
QUEUE_MAX_AGE_DAYS = 7


def queue_source_name(iso: str) -> str:
    """Source name in the ``source_health`` table (edgar uses 'edgar-fts')."""
    return f"queue-{iso.lower()}"


def evaluate_queue_source(
    iso: str,
    projects: Iterable[QueueProject],
    now: datetime,
    *,
    max_age_days: int = QUEUE_MAX_AGE_DAYS,
) -> SourceHealthRecord:
    """One ISO's queue health from its fetched projects.

    Empty → failed (format change, never a quiet week); stale when even the
    newest row is past the freshness budget; ok otherwise.
    """
    name = queue_source_name(iso)
    fetched = list(projects)
    if not fetched:
        return record_failure(
            name,
            "source returned zero projects — likely a format change, not an empty queue",
        )

    newest = max(p.retrieved_at for p in fetched)
    record = evaluate_freshness(name, newest, now, max_age_days)
    if record.status == "stale":
        return SourceHealthRecord(
            source_name=name,
            status="stale",
            detail=f"newest row {newest.strftime('%Y-%m-%dT%H:%M:%SZ')}; {record.detail}",
        )
    return record


def check_queue_source(
    iso: str,
    fetch: Callable[[], list[QueueProject]],
    now: datetime,
) -> SourceHealthRecord:
    """Fetch and evaluate in one step — any adapter error becomes a record.

    The broad except is the point: a failed fetch must SURFACE as a failed
    source-health record (which fails the scan), never vanish into a
    try/except that returns an empty project list.
    """
    try:
        projects = fetch()
    except Exception as exc:  # noqa: BLE001 — failure recording is the contract
        return record_failure(queue_source_name(iso), f"fetch failed: {exc}")
    return evaluate_queue_source(iso, projects, now)
