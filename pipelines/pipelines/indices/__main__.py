"""CLI: ``python -m pipelines.indices [flags]``.

Two modes:

* ``fixture`` (default) — compute from a checked-in closes CSV; no network,
  no secrets. This is what CI and the scheduled workflow run by default.
* ``live`` — fetch from Tiingo (``TIINGO_API_KEY`` required in the
  environment) with pacing, backoff, and the on-disk cache.

``--store`` publishes levels to Supabase and requires ``SUPABASE_DB_URL``;
like the weekly scan, it fails safely (clear parser error, no partial
writes) when the environment is missing.

The run fails loudly when an exclusion surprise appears (a constituent with
no data that is not documented, or a documented exclusion that now has
data): someone must reconcile ``index_exclusions.csv`` — never a silent
shrink.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from datetime import date
from pathlib import Path

from .benchmarks import INDEX_BENCHMARKS, BenchmarkSeries, rebase_proxy
from .compute import (
    BASE_DATES,
    ComputedIndex,
    compute_index,
    load_compositions,
    write_outputs,
)
from .engine import EngineError, EodRecord
from .exclusions import load_exclusions
from .replacements import load_replacements

logger = logging.getLogger(__name__)

FIXTURE_CSV = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "index" / "base_closes.csv"
)
DEFAULT_OUT_DIR = Path(__file__).resolve().parents[2] / "outputs" / "index"
DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[2] / "outputs" / "index_cache"
_REPLACEMENTS_CSV = Path(__file__).resolve().parents[2] / "data" / "index_replacements.csv"


def load_fixture_records(path: Path | str) -> dict[str, list[EodRecord]]:
    """Read the checked-in fixture closes into EOD records, grouped by ticker.

    Columns: ticker, universe, date, close, adjClose. The universe column is
    informational; group membership comes from the validated universe files.
    """
    records: dict[str, list[EodRecord]] = {}
    with Path(path).open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):
            try:
                record = EodRecord(
                    date.fromisoformat(row["date"].strip()),
                    float(row["close"]),
                    float(row["adjClose"]),
                )
            except (KeyError, ValueError) as exc:
                raise EngineError(f"fixture row {i}: malformed ({exc})") from exc
            records.setdefault(row["ticker"].strip().upper(), []).append(record)
    for ticker in records:
        records[ticker].sort(key=lambda r: r.date)
    return records


def finalize_run(
    out_dir: Path,
    source: str,
    indices: list[ComputedIndex],
    benchmarks: dict[str, list[BenchmarkSeries]],
) -> dict[str, str]:
    """Write outputs, but only after the exclusion contract is reconciled."""
    surprises = [
        (index.index_id, s.ticker)
        for index in indices
        for s in index.surprises
    ]
    if surprises:
        for index_id, ticker in surprises:
            logger.error(
                "EXCLUSION SURPRISE %s: %s — reconcile index_exclusions.csv "
                "(document the gap or restore the constituent), then re-run",
                index_id, ticker,
            )
        raise EngineError(f"exclusion surprises need reconciliation: {surprises}")
    return write_outputs(out_dir, indices, benchmarks, source)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pipelines.indices",
        description="Compute AS-100 / AS-MC100 daily index levels (Tiingo-fed).",
    )
    parser.add_argument(
        "--mode", choices=("fixture", "live"), default="fixture",
        help="fixture: offline from checked-in closes; live: fetch from Tiingo",
    )
    parser.add_argument("--fixture-file", default=str(FIXTURE_CSV))
    parser.add_argument("--universe-csv", default=None)
    parser.add_argument("--large-cap-csv", default=None)
    parser.add_argument("--replacements", default=None,
                        help="replacement feed CSV (default: "
                             "data/index_replacements.csv if present)")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--pace-seconds", type=float, default=3.0)
    parser.add_argument(
        "--store", action="store_true",
        help="publish levels to Supabase (requires SUPABASE_DB_URL)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    if args.mode == "live":
        api_key = os.environ.get("TIINGO_API_KEY", "")
        if not api_key:
            parser.error("live mode requires TIINGO_API_KEY in the environment")
        # Imported here so fixture mode never needs the fetcher module wired.
        from .fetcher import TiingoEodClient

        client = TiingoEodClient(api_key, args.cache_dir, pace_seconds=args.pace_seconds)
        compositions = load_compositions(args.universe_csv, args.large_cap_csv)
        all_tickers = sorted({t for tickers in compositions.values() for t in tickers})
        # Benchmark proxies share the client and cache.
        proxies = sorted({p for ps in INDEX_BENCHMARKS.values() for p in ps})
        start = min(BASE_DATES.values())
        end = date.today()
        records = client.fetch_range(all_tickers + proxies, start, end)
        source = "live-tiingo"
    else:
        records = load_fixture_records(args.fixture_file)
        source = "fixture"

    compositions = load_compositions(args.universe_csv, args.large_cap_csv)
    exclusions = load_exclusions()
    replacements_path = Path(args.replacements) if args.replacements else _REPLACEMENTS_CSV
    replacements = load_replacements(replacements_path)

    indices = [
        compute_index(
            index_id=index_id,
            composition=compositions[index_id],
            records=records,
            replacements=replacements,
            exclusions=exclusions,
        )
        for index_id in ("AS-100", "AS-MC100")
    ]

    benchmarks: dict[str, list[BenchmarkSeries]] = {}
    for index in indices:
        benchmarks[index.index_id] = [
            rebase_proxy(proxy, records.get(proxy, []), index.base_date)
            for proxy in INDEX_BENCHMARKS[index.index_id]
        ]

    hashes = finalize_run(Path(args.out_dir), source, indices, benchmarks)

    if args.store:
        db_url = os.environ.get("SUPABASE_DB_URL", "")
        if not db_url:
            parser.error("--store requires SUPABASE_DB_URL in the environment")
        _publish(db_url, indices)
    else:
        logger.info("skipping store publish (--store not given)")

    for name, digest in sorted(hashes.items()):
        print(f"{name}: {digest}")
    return 0


def _publish(db_url: str, indices: list[ComputedIndex]) -> None:
    try:
        import psycopg  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - env-dependent
        raise EngineError(
            "psycopg is not installed — install it to use --store, or run without --store"
        ) from exc
    from .store import publish_index_levels

    with psycopg.connect(db_url) as conn:  # type: ignore[attr-defined]
        result = publish_index_levels(conn, indices)
    logger.info(
        "published %d level rows; batch %s (hash %s)",
        result.rows, result.batch_id, result.batch_hash,
    )


if __name__ == "__main__":
    sys.exit(main())
