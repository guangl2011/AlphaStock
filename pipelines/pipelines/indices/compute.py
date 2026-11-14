"""Pipeline orchestration: universes in, deterministic index artifacts out.

A run consumes EOD records for every constituent (fixture or live Tiingo)
and produces three artifacts per run directory:

* ``index_levels.csv`` — daily levels for both indices (canonical format).
* ``index_metadata.json`` — base dates, exclusions, events, proxy mapping,
  and the SHA-256 attestation of the levels file.
* ``comparison.csv`` / ``comparison.json`` — index vs benchmark proxies for
  the Explorer comparison view.

Determinism contract: outputs are a pure function of the input records. No
wall-clock timestamps, no dict-order dependence, fixed float formatting —
running the same inputs twice produces byte-identical files.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .benchmarks import (
    INDEX_BENCHMARKS,
    PROXY_MAP,
    BenchmarkSeries,
    comparison_date_gaps,
    comparison_rows,
)
from .dates import rebalance_dates
from .engine import BASE_LEVEL, EngineError, EodRecord, EqualWeightEngine
from .exclusions import Exclusion, reconcile
from .replacements import Replacement, due_on

logger = logging.getLogger(__name__)

#: Methodology v4.0 — three equal-weight indices:
#: AS-100 (Large-Cap, every name >= $10B), AS-MCG100 (Mixed-Cap-Growth,
#: 40 Mid $2-10B / 40 Small $250M-2B / 20 Micro < $250M) and AS-MCB100
#: (Mixed-Cap-Balanced, 70 / 25 / 5). MCG and MCB may share names; neither
#: may hold an AS-100 constituent.
INDEX_IDS = ("AS-100", "AS-MCG100", "AS-MCB100")

#: Base dates: AS-100 at its 2026-11-09 construction close; the mixed-cap
#: indices at their 2026-11-13 construction close (universe screen as-of).
BASE_DATES: dict[str, date] = {
    "AS-100": date(2026, 11, 9),
    "AS-MCG100": date(2026, 11, 13),
    "AS-MCB100": date(2026, 11, 13),
}

#: Universe label used by index_exclusions.csv / index_replacements.csv.
UNIVERSE_LABELS: dict[str, str] = {
    "AS-100": "LargeCap100",
    "AS-MCG100": "MCG100",
    "AS-MCB100": "MCB100",
}

INDEX_NAMES: dict[str, str] = {
    "AS-100": "Large-Cap 100",
    "AS-MCG100": "Mixed-Cap-Growth 100",
    "AS-MCB100": "Mixed-Cap-Balanced 100",
}

METHODOLOGY_VERSION = "4.0"


@dataclass
class ComputedIndex:
    index_id: str
    base_date: date
    levels: dict[date, float]
    constituents: list[str]
    excluded: list[Exclusion] = field(default_factory=list)
    surprises: list[Exclusion] = field(default_factory=list)
    events: list = field(default_factory=list)


def load_compositions(
    mixed_cap_csv: Path | str | None = None,
    large_cap_csv: Path | str | None = None,
) -> dict[str, list[str]]:
    """Constituent lists per index from the checked-in, validated universe data.

    AS-100 reads ``large_cap_100.csv``; AS-MCG100 / AS-MCB100 read
    ``mixed_cap_universe.csv``. Disjointness from AS-100 is re-checked here.
    """
    # The `data` package (pipelines/data/) ships beside the package, not in
    # the wheel — same sys.path treatment tests get from conftest.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from data.large_cap import load_large_cap
    from data.mixed_cap import compositions as mixed_compositions
    from data.mixed_cap import load_mixed_cap

    large = load_large_cap(large_cap_csv) if large_cap_csv else load_large_cap()
    out = {"AS-100": sorted(e.ticker for e in large)}
    mixed = load_mixed_cap(mixed_cap_csv) if mixed_cap_csv else load_mixed_cap()
    out.update(mixed_compositions(mixed))
    for index_id in ("AS-MCG100", "AS-MCB100"):
        clash = sorted(set(out[index_id]) & set(out["AS-100"]))
        if clash:
            raise EngineError(f"{index_id} overlaps AS-100 (Large-Cap): {clash}")
    return out


def _effective_composition(
    index_id: str,
    composition: list[str],
    records: dict[str, list[EodRecord]],
    exclusions: list[Exclusion],
) -> tuple[list[str], list[Exclusion], list[Exclusion]]:
    """Resolve exclusions for one index; the remainder is what computes."""
    missing = {t for t in composition if not records.get(t)}
    effective, surprises = reconcile(
        exclusions, _universe_name(index_id), set(composition) - missing, missing
    )
    excluded_tickers = {e.ticker for e in effective}
    remaining = [t for t in composition if t not in excluded_tickers]
    if len(remaining) < 2:
        raise EngineError(
            f"{index_id}: only {len(remaining)} constituents remain after exclusions — "
            "refusing to compute an index this thin"
        )
    return remaining, effective, surprises


def _universe_name(index_id: str) -> str:
    # Exclusions CSV records the sheet's universe label.
    return UNIVERSE_LABELS[index_id]


def compute_index(
    index_id: str,
    composition: list[str],
    records: dict[str, list[EodRecord]],
    replacements: list[Replacement] | None = None,
    exclusions: list[Exclusion] | None = None,
) -> ComputedIndex:
    """Run one index from base date forward, with loud exclusions.

    Replacements take effect on the first trading date at or after their
    ``effective_date``; quarterly rebalances land on the last trading day of
    Mar/Jun/Sep/Dec observed in the data.
    """
    base_date = BASE_DATES[index_id]
    remaining, effective, surprises = _effective_composition(
        index_id, composition, records, exclusions or []
    )
    if surprises:
        # Undocumented data gap or a stale exclusion: fail before any
        # computation — the universe contract must be reconciled first.
        raise EngineError(
            f"{index_id}: exclusion surprises need reconciliation: "
            f"{sorted(s.ticker for s in surprises)}"
        )
    logger.info(
        "%s: computing over %d constituents (base %s)",
        index_id, len(remaining), base_date,
    )

    # Flat (ticker, date) lookup — built once, reused for every trading day.
    lookup: dict[tuple[str, date], EodRecord] = {}
    for ticker, recs in records.items():
        for record in recs:
            lookup[(ticker, record.date)] = record

    # Union of trading dates at/after base across the effective composition.
    all_dates: set[date] = set()
    for recs in records.values():
        all_dates.update(r.date for r in recs)
    trading_days = sorted(d for d in all_dates if d >= base_date)
    if not trading_days or trading_days[0] != base_date:
        raise EngineError(
            f"{index_id}: base date {base_date} has no complete closes — "
            "cannot establish the 1000.0 anchor"
        )

    # First trading date on/after each replacement's effective date.
    replacement_days: dict[date, list[Replacement]] = {}
    for rep in replacements or []:
        if rep.universe != _universe_name(index_id):
            continue
        future = [d for d in trading_days if d >= rep.effective_date]
        if not future:
            logger.warning(
                "%s: replacement %s -> %s (effective %s) has no trading day in "
                "the run window yet — it will apply on the next run",
                index_id, rep.remove_ticker, rep.add_ticker, rep.effective_date,
            )
            continue
        replacement_days.setdefault(future[0], []).append(rep)

    quarter_ends = rebalance_dates(trading_days)
    engine = EqualWeightEngine(composition=remaining)
    engine.initialize(base_date, _day_prices(lookup, remaining, base_date))

    levels: dict[date, float] = {base_date: BASE_LEVEL}
    current = list(remaining)
    for day in trading_days[1:]:
        prices = _day_prices(lookup, current, day)
        level = engine.step(day, prices)
        for rep in due_on(replacement_days.get(day, []), _universe_name(index_id), day):
            engine.apply_replacement(day, prices, rep.remove_ticker, rep.add_ticker)
            current = [t for t in current if t != rep.remove_ticker] + [rep.add_ticker]
        if day in quarter_ends:
            # Rebalances and replacements keep the level continuous by
            # construction, so the day's recorded level stays the step value.
            engine.rebalance(day, prices, current)
        levels[day] = level

    return ComputedIndex(
        index_id=index_id,
        base_date=base_date,
        levels=levels,
        constituents=current,
        excluded=effective,
        surprises=surprises,
        events=engine.events,
    )


def _day_prices(
    lookup: dict[tuple[str, date], EodRecord], tickers: list[str], day: date
) -> dict[str, EodRecord]:
    out: dict[str, EodRecord] = {}
    for t in tickers:
        record = lookup.get((t, day))
        if record is not None:
            out[t] = record
    return out


# -- deterministic serialization --------------------------------------------


def levels_csv(indices: list[ComputedIndex]) -> str:
    """Canonical levels CSV — byte-identical for identical inputs."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["index_id", "date", "level", "constituents"])
    for index in sorted(indices, key=lambda i: i.index_id):
        for day in sorted(index.levels):
            writer.writerow(
                [index.index_id, day.isoformat(), f"{index.levels[day]:.6f}",
                 len(index.constituents)]
            )
    return buf.getvalue()


def _index_payload(index: ComputedIndex) -> dict:
    return {
        "index_id": index.index_id,
        "base_date": index.base_date.isoformat(),
        "constituents": sorted(index.constituents),
        "constituent_count": len(index.constituents),
        "excluded": [
            {"ticker": e.ticker, "reason": e.reason, "first_observed": e.first_observed,
             "source": e.source}
            for e in sorted(index.excluded, key=lambda e: e.ticker)
        ],
        "surprises": [
            {"ticker": s.ticker, "reason": s.reason}
            for s in sorted(index.surprises, key=lambda s: s.ticker)
        ],
        "events": [
            {"date": ev.date.isoformat(), "kind": ev.kind, "detail": ev.detail}
            for ev in index.events
        ],
        "levels": {d.isoformat(): f"{index.levels[d]:.6f}" for d in sorted(index.levels)},
    }


def metadata_json(indices: list[ComputedIndex], source: str) -> str:
    """Canonical run metadata, including the proxy mapping and attestations."""
    levels = levels_csv(indices)
    payload = {
        "methodology_version": METHODOLOGY_VERSION,
        "source": source,
        "benchmark_proxy_mapping": dict(sorted(PROXY_MAP.items())),
        "benchmark_sets": {k: list(v) for k, v in sorted(INDEX_BENCHMARKS.items())},
        "sha256_index_levels_csv": hashlib.sha256(levels.encode()).hexdigest(),
        "indices": [_index_payload(i) for i in sorted(indices, key=lambda i: i.index_id)],
    }
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def comparison_csv(
    indices: list[ComputedIndex], benchmarks: dict[str, list[BenchmarkSeries]]
) -> str:
    """Canonical comparison CSV for the Explorer view."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["index_id", "date", "series", "level"])
    for index in sorted(indices, key=lambda i: i.index_id):
        rows = comparison_rows(index.index_id, index.levels, benchmarks[index.index_id])
        for row in rows:
            writer.writerow([index.index_id, row["date"], row["series"], f"{row['level']:.6f}"])
    return buf.getvalue()


def comparison_json(
    indices: list[ComputedIndex], benchmarks: dict[str, list[BenchmarkSeries]]
) -> str:
    payload = {
        "benchmark_proxy_mapping": dict(sorted(PROXY_MAP.items())),
        "series": {
            index.index_id: {
                "levels": {d.isoformat(): f"{index.levels[d]:.6f}" for d in sorted(index.levels)},
                "benchmarks": {
                    s.proxy: {
                        "name": s.name,
                        "levels": {d.isoformat(): f"{s.levels[d]:.6f}" for d in sorted(s.levels)},
                    }
                    for s in benchmarks[index.index_id]
                },
                "date_gaps": comparison_date_gaps(index.levels, benchmarks[index.index_id]),
            }
            for index in sorted(indices, key=lambda i: i.index_id)
        },
    }
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_outputs(
    out_dir: Path | str,
    indices: list[ComputedIndex],
    benchmarks: dict[str, list[BenchmarkSeries]],
    source: str,
) -> dict[str, str]:
    """Write the run's artifacts; returns {filename: sha256}."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = {
        "index_levels.csv": levels_csv(indices),
        "index_metadata.json": metadata_json(indices, source),
        "comparison.csv": comparison_csv(indices, benchmarks),
        "comparison.json": comparison_json(indices, benchmarks),
    }
    hashes: dict[str, str] = {}
    for name, content in files.items():
        (out / name).write_text(content, encoding="utf-8")
        hashes[name] = hashlib.sha256(content.encode()).hexdigest()
        logger.info("wrote %s (sha256 %s)", out / name, hashes[name])
    return hashes
