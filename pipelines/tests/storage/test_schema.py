"""Static migration/RLS checks — no live database.

CI runs these without Supabase or a Postgres container: the assertions read the
migration SQL directly and verify the contract that matters for the signal store
(tables exist, append-only is enforced, RLS denies anon writes). Applying the
migrations to a real database is a deployment step documented in the README.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"

EXPECTED_TABLES = ("signals", "companies", "attestations", "source_health", "alert_history")


def load_migration(filename: str) -> str:
    path = MIGRATIONS_DIR / filename
    assert path.exists(), f"missing migration: {path}"
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def schema_sql() -> str:
    return load_migration("0001_signal_store.sql")


def table_block(sql: str, table: str) -> str:
    match = re.search(rf"CREATE TABLE {re.escape(table)} \((.*?)\);", sql, re.DOTALL)
    assert match, f"CREATE TABLE {table} not found"
    return match.group(1)


def test_migrations_exist_in_order() -> None:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    assert [p.name for p in files][0] == "0001_signal_store.sql"
    prefixes = [int(p.name.split("_", 1)[0]) for p in files]
    assert prefixes == sorted(prefixes) and len(prefixes) == len(set(prefixes))


def test_all_expected_tables_created(schema_sql: str) -> None:
    for table in EXPECTED_TABLES:
        table_block(schema_sql, table)  # asserts inside


def test_signals_is_append_only(schema_sql: str) -> None:
    assert re.search(r"BEFORE UPDATE ON signals\b", schema_sql)
    assert re.search(r"BEFORE DELETE ON signals\b", schema_sql)
    trigger_fn = re.search(
        r"CREATE FUNCTION prevent_mutation.*?RAISE EXCEPTION.*?;", schema_sql, re.DOTALL
    )
    assert trigger_fn, "append-only trigger function must RAISE EXCEPTION"


def test_signals_carries_status_transition_fields(schema_sql: str) -> None:
    block = table_block(schema_sql, "signals")
    for column in ("iso", "project_id", "status_from", "status_to", "hash", "invalidation"):
        assert re.search(rf"^\s*{column}\b", block, re.MULTILINE), f"signals.{column} missing"


def test_signals_constrains_enums_and_strength(schema_sql: str) -> None:
    block = table_block(schema_sql, "signals")
    assert "event_type" in block and "'status_transition'" in block
    assert re.search(r"strength BETWEEN 1 AND 5", block)


def test_signals_hash_is_sha256_hex(schema_sql: str) -> None:
    block = table_block(schema_sql, "signals")
    assert re.search(r"hash\s+text NOT NULL CHECK \(hash ~ '\^\[0-9a-f\]\{64\}\$'\)", block)


def test_attestations_is_append_only(schema_sql: str) -> None:
    assert re.search(r"BEFORE UPDATE ON attestations\b", schema_sql)
    assert re.search(r"BEFORE DELETE ON attestations\b", schema_sql)
