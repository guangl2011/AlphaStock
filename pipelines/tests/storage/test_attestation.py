"""Attestation tests: hash stability and failed-write-no-publish.

These run against an in-memory fake connection — no live Supabase or Postgres
in CI. The fake records executed statements and transaction outcomes so the
publish contract (attestation written after signals, all-or-nothing commit)
can be asserted directly.
"""

from __future__ import annotations

import pytest

from pipelines.attestation import (
    PublishResult,
    batch_hash,
    canonical_json,
    is_sha256_hex,
    publish_with_attestation,
    signal_hash,
)


def make_signal(signal_id: str = "SIG-2026-11-000001", **overrides: object) -> dict[str, object]:
    signal: dict[str, object] = {
        "signal_id": signal_id,
        "category": "V2_INTERCONNECTION",
        "event_type": "status_transition",
        "tickers": ["GEV", "PWR"],
        "theme": "Grid & Transmission",
        "iso": "ERCOT",
        "project_id": "ERCOT-ABC-123",
        "status_from": "studied",
        "status_to": "esa_signed",
        "summary_en": "A large-load project signed an ercot interconnection agreement.",
        "summary_zh": "一个大负荷项目签署了ERCOT并网协议。",
        "direction": "positive",
        "strength": 4,
        "horizon_days": 90,
        "scenario_range": {"bear": -0.06, "base": 0.04, "bull": 0.15},
        "probability_edge": 0.58,
        "macro_overlay": {"rate_sensitivity": "high", "usd_sensitivity": "low"},
        "invalidation": "Project withdrawal or a two-quarter delay in energization.",
        "sources": [
            {
                "type": "ISO_queue",
                "url": "https://example.com/queue",
                "retrieved_at": "2026-11-08T13:30:00Z",
            }
        ],
        "verified": True,
    }
    signal.update(overrides)
    return signal


class FakeConnection:
    """In-memory PublishConnection double: records statements, can be made to fail."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.statements: list[tuple[str, tuple[object, ...] | None]] = []
        self.committed = False
        self.rolled_back = False
        self._fail_on = fail_on

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        if self._fail_on and self._fail_on in sql:
            raise RuntimeError(f"simulated failure: {self._fail_on}")
        self.statements.append((sql, params))

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def inserts_into(self, table: str) -> int:
        return sum(1 for sql, _ in self.statements if f"INSERT INTO {table}" in sql)


class TestHashStability:
    def test_batch_hash_stable_for_identical_input(self) -> None:
        signals = [make_signal("SIG-1"), make_signal("SIG-2")]
        assert batch_hash(signals) == batch_hash(signals)

    def test_batch_hash_is_order_independent(self) -> None:
        signals = [make_signal("SIG-1"), make_signal("SIG-2"), make_signal("SIG-3")]
        assert batch_hash(signals) == batch_hash(list(reversed(signals)))

    def test_batch_hash_changes_when_any_signal_changes(self) -> None:
        signals = [make_signal("SIG-1"), make_signal("SIG-2")]
        bumped = [make_signal("SIG-1"), make_signal("SIG-2", strength=5)]
        assert batch_hash(signals) != batch_hash(bumped)

    def test_batch_hash_is_sha256_hex(self) -> None:
        assert is_sha256_hex(batch_hash([make_signal()]))

    def test_empty_batch_is_defined(self) -> None:
        assert is_sha256_hex(batch_hash([]))
        assert batch_hash([]) == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    def test_signal_hash_stable_across_key_ordering(self) -> None:
        a = make_signal()
        b = dict(reversed(list(a.items())))
        assert signal_hash(a) == signal_hash(b)
        assert signal_hash(a) == signal_hash(dict(a))

    def test_canonical_json_is_deterministic_utf8(self) -> None:
        assert canonical_json({"b": 1, "a": "中文"}) == b'{"a":"\xe4\xb8\xad\xe6\x96\x87","b":1}'


class TestPublishWithAttestation:
    def test_success_writes_signals_then_attestation_and_commits(self) -> None:
        conn = FakeConnection()
        signals = [make_signal("SIG-1"), make_signal("SIG-2")]

        result = publish_with_attestation(conn, signals)

        assert isinstance(result, PublishResult)
        assert conn.inserts_into("signals") == 2
        assert conn.inserts_into("attestations") == 1
        # Attestation insert comes after every signal insert.
        sqls = [sql for sql, _ in conn.statements]
        last_signal_idx = max(i for i, s in enumerate(sqls) if "INSERT INTO signals" in s)
        attest_idx = next(i for i, s in enumerate(sqls) if "INSERT INTO attestations" in s)
        assert attest_idx > last_signal_idx
        assert conn.committed and not conn.rolled_back

    def test_signal_row_hashes_the_signal(self) -> None:
        conn = FakeConnection()
        signal = make_signal()

        result = publish_with_attestation(conn, [signal])

        params = conn.statements[0][1]
        assert params is not None
        assert params[0] == signal["signal_id"]
        assert params[1] == signal_hash(signal)
        assert result.batch_hash == batch_hash([signal])

    def test_failed_attestation_write_publishes_nothing(self) -> None:
        conn = FakeConnection(fail_on="INSERT INTO attestations")
        signals = [make_signal("SIG-1"), make_signal("SIG-2")]

        with pytest.raises(RuntimeError, match="simulated failure"):
            publish_with_attestation(conn, signals)

        assert conn.rolled_back
        assert not conn.committed
        # In a real transaction, rollback discards the signal inserts recorded above;
        # the contract is commit was never reached.
        assert conn.inserts_into("attestations") == 0

    def test_failed_signal_write_publishes_nothing(self) -> None:
        conn = FakeConnection(fail_on="INSERT INTO signals")
        with pytest.raises(RuntimeError, match="simulated failure"):
            publish_with_attestation(conn, [make_signal()])
        assert conn.rolled_back and not conn.committed

    def test_empty_batch_is_refused(self) -> None:
        conn = FakeConnection()
        with pytest.raises(ValueError, match="empty batch"):
            publish_with_attestation(conn, [])
        assert not conn.committed and not conn.rolled_back

    def test_missing_required_field_is_refused(self) -> None:
        conn = FakeConnection()
        with pytest.raises(ValueError, match="invalidation"):
            publish_with_attestation(conn, [make_signal(invalidation="")])
        assert not conn.committed

    def test_batch_id_is_deterministic_from_batch_hash(self) -> None:
        signals = [make_signal("SIG-1")]
        conn_a, conn_b = FakeConnection(), FakeConnection()
        first = publish_with_attestation(conn_a, signals)
        second = publish_with_attestation(conn_b, list(signals))
        assert first.batch_id == second.batch_id
        assert first.batch_id == f"batch-{first.batch_hash[:24]}"
