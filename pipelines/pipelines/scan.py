"""Weekly scan runner: V2 queue diff + V4 pilot legs behind a hard health gate.

Composes the already-merged pipeline pieces (queue adapters, diff, EDGAR
sweep, county watchlist, attested publish) into the Monday scan the build
spec's workflow describes. The contract that matters (build spec, "Named
failure cases"):

* every source is health-checked — stale or failed turns the scan red via
  ``assert_scan_healthy``, raised BEFORE anything publishes;
* a quiet week with healthy sources is a legitimate empty scan — an honest
  zero after green checks, never a silent one;
* publishing goes through ``publish_with_attestation`` (hash-before-serve)
  when a store connection is provided; without one (fixtures/dispatch runs)
  the scan returns its outputs for the report and snapshot rotation.

The CLI entrypoint (``python -m pipelines.scan``) owns all I/O — live
gridstatus/EDGAR fetches, fixture loading, output files — so the scan core
stays deterministic and testable.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipelines.attestation.publish import (
    PublishConnection,
    PublishResult,
    publish_with_attestation,
)
from pipelines.ingest.counties import WatchlistCounty, ingest_announcements, load_watchlist
from pipelines.ingest.edgar import SWEEP_KEYWORDS, UniverseLike, sweep
from pipelines.ingest.queues import ALL_ISOS, QueueProject, build_adapter
from pipelines.ingest.queues.health import evaluate_queue_source
from pipelines.ingest.source_health import (
    SourceHealthRecord,
    assert_scan_healthy,
    record_failure,
)
from pipelines.signals.queue_diff import diff_projects, index_projects

_SCAN_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

#: Previous-scan baseline committed by the last run (see snapshot rotation).
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "data" / "snapshots" / "queue_snapshot.json"

#: Golden fixtures used by ``--mode fixtures`` (committed, no network).
FIXTURE_QUEUE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "queues"
FIXTURE_EDGAR = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "edgar" / "fts_responses.json"
)
FIXTURE_PERMITS = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "permits" / "announcements.json"
)


@dataclass(frozen=True)
class ScanResult:
    """Everything one scan observed, ready for report, snapshot, and store."""

    signals: list[dict[str, Any]]
    health: list[SourceHealthRecord]
    #: The current snapshot index — next week's diff baseline.
    snapshot_index: dict[str, QueueProject]
    published: PublishResult | None = None
    batch_hash: str | None = None


@dataclass
class _StoreHandle:
    """Adapter exposing a psycopg connection as the publish protocol."""

    conn: Any

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> Any:
        return self.conn.execute(sql, params)

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()


def connect_store(db_url: str) -> PublishConnection:
    """Open a store connection for the publish path (optional ``[store]`` extra).

    Kept lazy so tests, fixtures-mode runs, and environments without the
    Supabase DB never import psycopg.
    """
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - depends on env, not logic
        raise RuntimeError(
            "publish requested but psycopg is not installed — pip install "
            '"alphastock-pipelines[store]"'
        ) from exc
    return _StoreHandle(psycopg.connect(db_url))


# --- Snapshot rotation -------------------------------------------------------


def snapshot_payload(index: Mapping[str, QueueProject]) -> dict[str, Any]:
    """Serialize a project index for next week's diff baseline (sorted, stable)."""
    projects = []
    for key in sorted(index):
        project = index[key]
        projects.append(
            {
                "iso": project.iso,
                "project_id": project.project_id,
                "name": project.name,
                "capacity_mw": project.capacity_mw,
                "load_type": project.load_type,
                "data_center_likelihood": project.data_center_likelihood,
                "status": project.status,
                "status_norm": project.status_norm,
                "county": project.county,
                "state": project.state,
                "raw": project.raw,
                "source_url": project.source_url,
                "retrieved_at": project.retrieved_at.strftime(_SCAN_TIME_FORMAT),
            }
        )
    return {"projects": projects}


def write_snapshot(index: Mapping[str, QueueProject], path: Path | str) -> Path:
    """Snapshot write via tmp file + replace, so a crash never leaves a half file."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(
        json.dumps(snapshot_payload(index), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    tmp.replace(out)
    return out


def load_snapshot_index(path: Path | str) -> dict[str, QueueProject]:
    """Load a previous scan's snapshot as the diff baseline."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    projects = [
        QueueProject(
            iso=row["iso"],
            project_id=row["project_id"],
            name=row["name"],
            capacity_mw=row.get("capacity_mw"),
            load_type=row.get("load_type", "unknown"),
            data_center_likelihood=float(row.get("data_center_likelihood", 0.0)),
            status=row.get("status", ""),
            status_norm=row["status_norm"],
            county=row.get("county"),
            state=row.get("state"),
            raw=row.get("raw", {}),
            source_url=row["source_url"],
            retrieved_at=datetime.strptime(row["retrieved_at"], _SCAN_TIME_FORMAT).replace(
                tzinfo=UTC
            ),
        )
        for row in raw["projects"]
    ]
    return index_projects(projects)


# --- The scan ----------------------------------------------------------------


def run_weekly_scan(
    iso_projects: Mapping[str, list[QueueProject]],
    prev_index: Mapping[str, QueueProject],
    *,
    watchlist: Sequence[WatchlistCounty],
    county_records: list[dict[str, object]] | None,
    edgar_responses: Sequence[tuple[str, Mapping[str, object]]] | None,
    universe: Sequence[UniverseLike],
    now: datetime,
    conn: PublishConnection | None = None,
    prefetch_health: Sequence[SourceHealthRecord] = (),
) -> ScanResult:
    """Run one weekly scan across the 7 ISOs and the V4 pilot legs.

    ``iso_projects`` maps each ISO to its fetched projects; the CLI wraps
    live-fetch errors into ``prefetch_health`` failure records so a failed
    fetch fails the scan instead of reading as an empty queue.
    ``county_records`` / ``edgar_responses`` of ``None`` mean the leg is not
    running this scan (config, not silence).

    Raises :class:`pipelines.ingest.source_health.SourceHealthError` on any
    stale or failed source — the red-workflow gate — before any publish.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    health: list[SourceHealthRecord] = list(prefetch_health)
    for iso in ALL_ISOS:
        health.append(evaluate_queue_source(iso, iso_projects.get(iso, []), now))

    all_projects: list[QueueProject] = []
    for iso in ALL_ISOS:
        all_projects.extend(iso_projects.get(iso, []))

    # One diff across the full current snapshot; rows carry iso per project.
    signals: list[dict[str, Any]] = list(diff_projects(prev_index, all_projects))

    county_signals, county_health = ingest_announcements(county_records or [], list(watchlist), now)
    health.extend(county_health)
    signals.extend(county_signals)

    edgar_signals, edgar_health = sweep(list(edgar_responses or []), universe, now)
    health.extend(edgar_health)
    signals.extend(edgar_signals)

    # The red-workflow gate: stale OR failed sources stop the scan here —
    # nothing is published or reported as a quiet week past this line.
    assert_scan_healthy(health)

    stamped = [dict(signal, published_at=now.strftime(_SCAN_TIME_FORMAT)) for signal in signals]

    published: PublishResult | None = None
    if conn is not None:
        _persist_source_health(conn, health, now)
        published = publish_with_attestation(conn, stamped) if stamped else None
        conn.commit()

    return ScanResult(
        signals=stamped,
        health=health,
        snapshot_index=index_projects(all_projects),
        published=published,
        batch_hash=published.batch_hash if published else None,
    )


_UPSERT_HEALTH_SQL = """\
INSERT INTO source_health (source_name, status, last_success_at, detail, checked_at)
VALUES (%s, %s, %s, %s, now())
ON CONFLICT (source_name) DO UPDATE
SET status = EXCLUDED.status,
    last_success_at = EXCLUDED.last_success_at,
    detail = EXCLUDED.detail,
    checked_at = now()"""


def _persist_source_health(
    conn: PublishConnection, health: Sequence[SourceHealthRecord], now: datetime
) -> None:
    """Upsert every source check — source_health is mutable state, not a ledger."""
    now_text = now.strftime(_SCAN_TIME_FORMAT)
    for record in health:
        last_success = now_text if record.status == "ok" else None
        conn.execute(
            _UPSERT_HEALTH_SQL,
            (record.source_name, record.status, last_success, record.detail),
        )


# --- CLI I/O helpers ---------------------------------------------------------


def fetch_live_queues(
    now: datetime,
) -> tuple[dict[str, list[QueueProject]], list[SourceHealthRecord]]:
    """Fetch all 7 ISO queues live; a failed fetch becomes a failed health record."""
    projects_by_iso: dict[str, list[QueueProject]] = {}
    health: list[SourceHealthRecord] = []
    for iso in ALL_ISOS:
        try:
            projects_by_iso[iso] = build_adapter(iso, None, now).fetch()
        except Exception as exc:  # the point: surface ANY fetch error as a failed source
            projects_by_iso[iso] = []
            health.append(record_failure(f"queue-{iso.lower()}", f"live fetch failed: {exc}"))
    return projects_by_iso, health


def fetch_live_edgar(timeout_seconds: int = 30) -> list[tuple[str, dict[str, object]]]:
    """Fetch EDGAR FTS responses for each sweep keyword (thin, loud on failure)."""
    import urllib.parse
    import urllib.request

    responses: list[tuple[str, dict[str, object]]] = []
    for keyword in SWEEP_KEYWORDS:
        query = urllib.parse.urlencode({"q": f'"{keyword}"', "forms": "8-K"})
        request = urllib.request.Request(
            f"https://efts.sec.gov/LATEST/search-index?{query}",
            headers={
                "User-Agent": "AlphaStock weekly scan (+contact: research@alphastock.example)"
            },
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            responses.append((keyword, json.load(response)))
    return responses


def write_json(path: Path | str, payload: object) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


# --- CLI ---------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pipelines.scan",
        description="Weekly scan: V2 queue diff across the 7 ISOs + V4 pilot, behind a "
        "source-health gate that turns stale/failed sources into a red run.",
    )
    parser.add_argument("--mode", choices=("live", "fixtures"), default="live")
    parser.add_argument("--all-isos", action="store_true", help="scan all 7 ISO queues")
    parser.add_argument("--edgar", action="store_true", help="run the EDGAR FTS sweep leg")
    parser.add_argument(
        "--permit-records",
        type=Path,
        default=None,
        help="JSON array of permit announcements for the county-watchlist leg "
        "(no live collector yet; omit to skip the leg)",
    )
    parser.add_argument("--snapshot", type=Path, default=None, help="previous scan's snapshot")
    parser.add_argument("--snapshot-out", type=Path, default=None)
    parser.add_argument("--signals-out", type=Path, default=None)
    parser.add_argument("--health-out", type=Path, default=None)
    parser.add_argument(
        "--as-of",
        type=str,
        default=None,
        help="scan instant (ISO, default now UTC) — pin it in fixtures mode for determinism",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="publish signals + source health to the store (requires SUPABASE_DB_URL)",
    )
    args = parser.parse_args(argv)

    if not args.all_isos:
        parser.error("--all-isos is required: the weekly scan covers all 7 ISOs")

    now = (
        datetime.fromisoformat(args.as_of.strip().replace("Z", "+00:00"))
        if args.as_of
        else datetime.now(UTC)
    )
    if now.tzinfo is None:
        parser.error("--as-of must be timezone-aware")

    # Sources: live fetches or committed golden fixtures. Fetch failures become
    # failed health records (prefetch_health) — the gate below turns them red.
    prefetch_health: list[SourceHealthRecord] = []
    if args.mode == "fixtures":
        if not FIXTURE_QUEUE_DIR.is_dir():
            raise SystemExit(f"fixtures not found: {FIXTURE_QUEUE_DIR}")

        def fixture_records(iso: str) -> list[dict[str, Any]]:
            stem = iso.lower().replace("-", "")  # ISO-NE -> isone_snapshot.json
            path = FIXTURE_QUEUE_DIR / f"{stem}_snapshot.json"
            return json.loads(path.read_text(encoding="utf-8"))

        projects_by_iso = {
            iso: build_adapter(iso, lambda i=iso: fixture_records(i), now).fetch()
            for iso in ALL_ISOS
        }
    else:
        projects_by_iso, prefetch_health = fetch_live_queues(now)

    prev_index: dict[str, QueueProject] = {}
    snapshot_path = args.snapshot or (SNAPSHOT_PATH if SNAPSHOT_PATH.is_file() else None)
    if snapshot_path and Path(snapshot_path).is_file():
        prev_index = load_snapshot_index(snapshot_path)

    # V4 legs. A leg that is skipped is a plan decision (county_records/responses
    # stay None); a leg that fails its fetch becomes a failed record, not a skip.
    edgar_responses: list[tuple[str, dict[str, object]]] | None = None
    if args.edgar:
        if args.mode == "fixtures":
            edgar_responses = json.loads(FIXTURE_EDGAR.read_text(encoding="utf-8"))
        else:
            try:
                edgar_responses = fetch_live_edgar()
            except Exception as exc:
                prefetch_health.append(record_failure("edgar-fts", f"live fetch failed: {exc}"))
                edgar_responses = []

    county_records: list[dict[str, object]] | None = None
    if args.permit_records is not None:
        county_records = json.loads(Path(args.permit_records).read_text(encoding="utf-8"))
    elif args.mode == "fixtures":
        county_records = json.loads(FIXTURE_PERMITS.read_text(encoding="utf-8"))

    # The `data` package (pipelines/data/) ships beside the package, not in the
    # wheel — same sys.path treatment tests get from conftest.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from data.universe import load_universe

    conn = None
    if args.publish:
        db_url = os.environ.get("SUPABASE_DB_URL")
        if not db_url:
            parser.error("--publish requires SUPABASE_DB_URL in the environment")
        conn = connect_store(db_url)

    result = run_weekly_scan(
        projects_by_iso,
        prev_index,
        watchlist=load_watchlist(),
        county_records=county_records,
        edgar_responses=edgar_responses,
        universe=load_universe(),
        now=now,
        conn=conn,
        prefetch_health=prefetch_health,
    )

    if args.signals_out:
        write_json(args.signals_out, result.signals)
    if args.health_out:
        write_json(
            args.health_out,
            [
                {
                    "source_name": r.source_name,
                    "status": r.status,
                    "detail": r.detail,
                }
                for r in result.health
            ],
        )
    if args.snapshot_out:
        write_snapshot(result.snapshot_index, args.snapshot_out)

    published_word = (
        f"published batch {result.batch_hash}"
        if result.published
        else (
            "no store connection — outputs written for the report stage"
            if result.signals
            else "quiet week: zero qualifying signals from healthy sources"
        )
    )
    print(f"scan ok: {len(result.signals)} signal(s); {published_word}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
