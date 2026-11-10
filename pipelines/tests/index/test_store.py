"""Tests for the index store writer (transactional publish + attestation)."""

from __future__ import annotations

from datetime import date

import pytest

from pipelines.indices.compute import ComputedIndex
from pipelines.indices.engine import EngineError
from pipelines.indices.exclusions import Exclusion
from pipelines.indices.store import publish_index_levels


class FakeConnection:
    def __init__(self, fail_on_execute: int | None = None) -> None:
        self.executed: list[tuple[str, tuple[object, ...] | None]] = []
        self.committed = 0
        self.rolled_back = 0
        self._fail_on = fail_on_execute

    def execute(self, sql, params=None):
        if self._fail_on is not None and len(self.executed) == self._fail_on:
            raise RuntimeError("boom")
        self.executed.append((sql, params))

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        self.rolled_back += 1


def _index(index_id: str = "AS-100", excluded: list[Exclusion] | None = None) -> ComputedIndex:
    return ComputedIndex(
        index_id=index_id,
        base_date=date(2026, 11, 6),
        levels={date(2026, 11, 6): 1000.0, date(2026, 11, 9): 1005.123456789},
        constituents=[f"T{i}" for i in range(99)],
        excluded=excluded or [],
    )


class TestPublish:
    def test_inserts_levels_and_attestation_then_commits(self) -> None:
        conn = FakeConnection()
        result = publish_index_levels(conn, [_index()])
        assert conn.committed == 1
        assert conn.rolled_back == 0
        level_rows = [p for (sql, p) in conn.executed if "index_levels" in sql]
        attestation = [p for (sql, p) in conn.executed if "index_attestations" in sql]
        assert len(level_rows) == 2
        # Level formatted canonically to 6 dp.
        assert level_rows[0][2] == "1000.000000"
        assert level_rows[1][2] == "1005.123457"
        # Attestation carries batch hash + index ids.
        assert attestation[0][3] == ["AS-100"]
        assert len(result.batch_hash) == 64
        assert result.batch_id == f"idx-{result.batch_hash[:16]}"

    def test_excluded_tickers_are_stored_on_each_row(self) -> None:
        conn = FakeConnection()
        exclusion = Exclusion("PSTG", "LargeCap100", "Tiingo EOD gap", "2026-11-10", "source")
        publish_index_levels(conn, [_index("AS-MC100", [exclusion])])
        level_rows = [p for (sql, p) in conn.executed if "index_levels" in sql]
        assert level_rows[0][4] == ["PSTG"]

    def test_level_hash_is_deterministic_and_sensitive_to_level(self) -> None:
        conn = FakeConnection()
        publish_index_levels(conn, [_index()])
        hashes = [p[5] for (sql, p) in conn.executed if "index_levels" in sql]
        conn2 = FakeConnection()
        publish_index_levels(conn2, [_index()])
        hashes2 = [p[5] for (sql, p) in conn2.executed if "index_levels" in sql]
        assert hashes == hashes2

        moved = _index()
        moved.levels[date(2026, 11, 9)] = 1007.5  # differs at 6dp canonical form
        conn3 = FakeConnection()
        publish_index_levels(conn3, [moved])
        hashes3 = [p[5] for (sql, p) in conn3.executed if "index_levels" in sql]
        assert hashes != hashes3

    def test_failure_rolls_back_everything(self) -> None:
        conn = FakeConnection(fail_on_execute=1)  # second level insert fails
        with pytest.raises(RuntimeError, match="boom"):
            publish_index_levels(conn, [_index()])
        assert conn.rolled_back == 1
        assert conn.committed == 0

    def test_empty_batch_refused(self) -> None:
        with pytest.raises(EngineError, match="empty index batch"):
            publish_index_levels(FakeConnection(), [])
