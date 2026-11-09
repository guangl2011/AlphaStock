"""Transactional publish: signals and their batch attestation commit together.

Hash-before-serve contract (MVP spec): the attestation row is written in the
same transaction path as the signal inserts. If the attestation write fails,
the whole transaction rolls back — a signal is never served without its hash
in the append-only ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .hashing import batch_hash, is_sha256_hex, signal_hash

REQUIRED_SIGNAL_FIELDS = ("signal_id", "summary_en", "summary_zh", "invalidation")

# Column order matches _signal_row(); one statement per signal.
_INSERT_SIGNAL_SQL = """\
INSERT INTO signals (signal_id, hash, category, event_type, tickers, theme, iso, project_id,
                     status_from, status_to, summary_en, summary_zh, direction, strength,
                     horizon_days, scenario_range, probability_edge, macro_overlay,
                     invalidation, sources, verified, published_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
        now())"""

_INSERT_ATTESTATION_SQL = """\
INSERT INTO attestations (batch_id, batch_hash, signal_count, signal_ids)
VALUES (%s, %s, %s, %s)"""


class PublishConnection(Protocol):
    """Minimal transactional connection: execute + commit + rollback.

    Works directly with a psycopg connection wrapper; see README for the
    three-line adapter when using psycopg's `conn.execute()` API.
    """

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> Any: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


@dataclass(frozen=True)
class PublishResult:
    signal_ids: list[str]
    batch_id: str
    batch_hash: str


def _signal_row(signal: dict[str, Any]) -> tuple[object, ...]:
    missing = [f for f in REQUIRED_SIGNAL_FIELDS if not signal.get(f)]
    if missing:
        raise ValueError(f"signal missing required fields: {missing}")
    digest = signal_hash(signal)
    if not is_sha256_hex(digest):  # pragma: no cover - invariant, kept as a loud guard
        raise ValueError(f"computed signal hash is not sha256 hex: {digest!r}")
    return (
        signal["signal_id"],
        digest,
        signal.get("category", "other"),
        signal.get("event_type", "other"),
        list(signal.get("tickers", [])),
        signal.get("theme"),
        signal.get("iso"),
        signal.get("project_id"),
        signal.get("status_from"),
        signal.get("status_to"),
        signal["summary_en"],
        signal["summary_zh"],
        signal.get("direction", "neutral"),
        signal.get("strength", 1),
        signal.get("horizon_days"),
        signal.get("scenario_range"),
        signal.get("probability_edge"),
        signal.get("macro_overlay", {}),
        signal["invalidation"],
        list(signal.get("sources", [])),
        signal.get("verified", False),
    )


def publish_with_attestation(
    conn: PublishConnection, signals: list[dict[str, Any]]
) -> PublishResult:
    """Insert signals and their batch attestation, committing atomically.

    The batch_id is derived from the batch hash, so re-publishing an identical
    batch is idempotent at the ledger level. Any failure — including the
    attestation insert — rolls back every signal insert.
    """
    if not signals:
        raise ValueError("refusing to publish an empty batch")

    try:
        for signal in signals:
            conn.execute(_INSERT_SIGNAL_SQL, _signal_row(signal))

        digest = batch_hash(signals)
        signal_ids = [s["signal_id"] for s in signals]
        batch_id = f"batch-{digest[:24]}"
        conn.execute(_INSERT_ATTESTATION_SQL, (batch_id, digest, len(signals), signal_ids))
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return PublishResult(signal_ids=signal_ids, batch_id=batch_id, batch_hash=digest)
