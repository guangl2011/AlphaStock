"""Source-health checks shared by all ingestion pipelines.

Named failure mode of this product category (build spec, "Risks"): a source
that changes format or goes stale must turn the scan red — never silently
produce zero signals. Status values match the ``source_health`` table in
``db/migrations/0001_signal_store.sql``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

VALID_STATUSES = ("ok", "stale", "failed")


class SourceHealthError(RuntimeError):
    """Raised when a source failure must fail the scan loudly."""


@dataclass(frozen=True)
class SourceHealthRecord:
    """One source's health, as stored in the ``source_health`` table."""

    source_name: str
    status: str  # 'ok' | 'stale' | 'failed'
    detail: str

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(
                f"source {self.source_name!r}: status {self.status!r} "
                f"not in {VALID_STATUSES}"
            )
        if not self.detail:
            raise ValueError(f"source {self.source_name!r}: empty detail")


def evaluate_freshness(
    source_name: str,
    last_success_at: datetime,
    now: datetime,
    max_age_days: int = 7,
) -> SourceHealthRecord:
    """OK while the source returned data within ``max_age_days``, stale after."""
    age_days = (now - last_success_at).days
    if age_days <= max_age_days:
        return SourceHealthRecord(
            source_name=source_name, status="ok", detail=f"last success {age_days}d ago"
        )
    return SourceHealthRecord(
        source_name=source_name,
        status="stale",
        detail=f"last success {age_days}d ago exceeds {max_age_days}d budget",
    )


def record_failure(source_name: str, detail: str) -> SourceHealthRecord:
    """A failed fetch/parse — the scan must surface it, never swallow it."""
    return SourceHealthRecord(source_name=source_name, status="failed", detail=detail)


def assert_healthy(records: list[SourceHealthRecord]) -> None:
    """Raise if any source failed, listing them — a red scan beats silent rot."""
    failed = [r for r in records if r.status == "failed"]
    if failed:
        lines = "; ".join(f"{r.source_name}: {r.detail}" for r in failed)
        raise SourceHealthError(f"source-health failure(s): {lines}")


def assert_scan_healthy(records: list[SourceHealthRecord]) -> None:
    """Scan-path gate: stale OR failed records turn the workflow red.

    Stricter than :func:`assert_healthy`, which treats stale as a warning for
    publish paths. A scan that ingests from an external source cannot trust
    data past its freshness budget — silent data rot is the named failure
    mode of this product category (build spec, "Risks") — so the weekly scan
    fails loudly on staleness too. Records keep their precise status
    ('stale' vs 'failed') for the source_health table; the raised error is
    what turns the workflow red.
    """
    unhealthy = [r for r in records if r.status != "ok"]
    if unhealthy:
        lines = "; ".join(f"{r.source_name} [{r.status}]: {r.detail}" for r in unhealthy)
        raise SourceHealthError(f"source-health failure(s): {lines}")
