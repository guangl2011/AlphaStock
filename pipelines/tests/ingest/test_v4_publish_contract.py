"""Contract test: V4 pilot events publish through the store's publish path.

The canonical event contract is the signals table (db/migrations/0001_signal_store.sql);
publish_with_attestation is its Python gate (hash-before-serve). Both V4 legs must
clear it with their fixture events — otherwise "same contract" is an assumption.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from data.universe import load_universe
from pipelines.attestation.publish import PublishResult, publish_with_attestation
from pipelines.ingest.counties import ingest_announcements, load_watchlist
from pipelines.ingest.edgar import sweep

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


@dataclass
class FakeConnection:
    """Records executed statements; commit/rollback outcomes are assertable."""

    statements: list[tuple[str, tuple[object, ...] | None]] = field(default_factory=list)
    committed: bool = False

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        self.statements.append((sql, params))

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        raise AssertionError("rollback must not be reached on a clean publish")


def test_v4_events_publish_through_the_store_contract() -> None:
    universe = load_universe()
    edgar_signals, edgar_health = sweep(
        [
            (
                "data center",
                {
                    "hits": {
                        "hits": [
                            {
                                "_id": "0001193125-26-123456:dform8k.htm",
                                "_excerpt": "expansion of our data center campus in Virginia",
                                "_source": {
                                    "cik": "0001297996",
                                    "display_names": ["Digital Realty Trust, Inc."],
                                    "file_type": "8-K",
                                    "file_date": "2026-11-03",
                                },
                            }
                        ]
                    }
                },
            )
        ],
        universe,
        NOW,
    )
    county_signals, county_health = ingest_announcements(
        [
            {
                "county": "Loudoun",
                "state": "Virginia",
                "date": "2026-11-05",
                "headline": "Board approves special exception for data center campus",
                "detail": "Campus will support an initial 120 MW of critical load.",
                "url": "https://www.loudoun.gov/agenda/2026-11-05-dc-campus",
            }
        ],
        load_watchlist(),
        NOW,
    )

    assert edgar_signals and county_signals
    signals = edgar_signals + county_signals

    conn = FakeConnection()
    result = publish_with_attestation(conn, signals)  # type: ignore[arg-type]
    assert isinstance(result, PublishResult)
    assert conn.committed
    # One INSERT per signal plus one attestation row, all in one transaction path.
    inserts = [sql for sql, _ in conn.statements if sql.startswith("INSERT INTO signals")]
    attestations = [sql for sql, _ in conn.statements if sql.startswith("INSERT INTO attestations")]
    assert len(inserts) == len(signals) == 2
    assert len(attestations) == 1
