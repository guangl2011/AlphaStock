"""Transactional publish of daily index levels: rows + batch attestation.

Mirrors the signal-store contract from PR #3 (``attestation.publish``): the
attestation row is written in the same transaction path as the level
inserts; if the attestation write fails, everything rolls back. A level is
never served without its hash in the append-only ledger. ``index_levels``
and ``index_attestations`` are append-only by trigger (migration 0003).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from .compute import ComputedIndex
from .engine import EngineError

_INSERT_LEVEL_SQL = """\
INSERT INTO index_levels (index_id, date, level, constituent_count, excluded, hash)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (index_id, date) DO NOTHING"""

_INSERT_ATTESTATION_SQL = """\
INSERT INTO index_attestations (batch_id, batch_hash, index_count, index_ids)
VALUES (%s, %s, %s, %s)"""


class StoreConnection:
    """Structural twin of ``attestation.publish.PublishConnection``.

    Declared locally so the indices package needs no import from the
    signals package; any psycopg-style connection satisfies it.
    """

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> Any: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


@dataclass(frozen=True)
class IndexPublishResult:
    rows: int
    batch_id: str
    batch_hash: str


def _level_row(index: ComputedIndex, day: object, level: float) -> tuple[object, ...]:
    excluded = sorted(e.ticker for e in index.excluded)
    level_hash = hashlib.sha256(
        "|".join([
            index.index_id, day.isoformat(), f"{level:.6f}",
            str(len(index.constituents)), ",".join(excluded),
        ]).encode()
    ).hexdigest()
    return (
        index.index_id,
        day.isoformat(),
        f"{level:.6f}",
        len(index.constituents),
        list(excluded),
        level_hash,
    )


def publish_index_levels(
    conn: StoreConnection, indices: list[ComputedIndex]
) -> IndexPublishResult:
    """Insert all level rows plus one batch attestation, committing atomically."""
    if not indices:
        raise EngineError("refusing to publish an empty index batch")

    rows: list[tuple[object, ...]] = []
    row_hashes: list[str] = []
    for index in sorted(indices, key=lambda i: i.index_id):
        for day in sorted(index.levels):
            row = _level_row(index, day, index.levels[day])
            rows.append(row)
            row_hashes.append(row[-1])

    digest_text = "\n".join(sorted(row_hashes))
    batch_hash = hashlib.sha256(digest_text.encode()).hexdigest()
    batch_id = f"idx-{batch_hash[:16]}"

    try:
        for row in rows:
            conn.execute(_INSERT_LEVEL_SQL, row)
        conn.execute(
            _INSERT_ATTESTATION_SQL,
            (batch_id, batch_hash, len(indices), [i.index_id for i in indices]),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return IndexPublishResult(rows=len(rows), batch_id=batch_id, batch_hash=batch_hash)


def exclusion_tickers(index: ComputedIndex) -> list[str]:
    """Sorted excluded tickers for one index (helper for callers/tests)."""
    return sorted(e.ticker for e in index.excluded)
