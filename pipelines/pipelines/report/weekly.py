"""Weekly report: build, render, and write the aggregate report.

``build_weekly_report`` composes the pure aggregates in
``report/aggregates.py`` into one deterministic ``WeeklyReport``;
``render_markdown`` renders it for the committed public page and
``report_payload`` serializes it for the Explorer's ``/weekly`` route. Both
outputs are derived from the same built report, so the page and the queryable
JSON can never disagree.

Source health is a first-class output (build spec): the report carries every
source's status verbatim and exposes ``healthy`` — but the RED/ok decision
belongs to the scan's ``assert_scan_healthy`` gate. This module renders
health; it never swallows a failure into a green report.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from pipelines.ingest.source_health import SourceHealthRecord
from pipelines.report.aggregates import (
    V2_CATEGORY,
    V4_CATEGORY,
    IsoQueueEvents,
    LagTracker,
    PilotMetrics,
    ThemeDelta,
    batch_hash_for_rows,
    earnings_lags,
    in_window,
    parse_published_at,
    queue_events_per_iso,
    score_movers,
    theme_heat_delta,
    v4_pilot_metrics,
)
from pipelines.scoring.alpha_score import ScoreDelta

#: Default aggregate window: one week, matching the Monday scan cadence.
DEFAULT_WINDOW_DAYS = 7

#: Default movers table depth.
DEFAULT_TOP_MOVERS = 10


@dataclass(frozen=True)
class WeeklyReport:
    """One week's aggregate report — the /weekly route's queryable shape."""

    generated_at: datetime
    window_start: date
    window_end: date
    total_signals: int
    queue_events: list[IsoQueueEvents]
    theme_deltas: list[ThemeDelta]
    movers: list[ScoreDelta]
    pilot: PilotMetrics
    health: list[SourceHealthRecord]
    lag: LagTracker
    batch_hash: str
    categories: dict[str, int] = field(default_factory=dict)

    @property
    def healthy(self) -> bool:
        """True when every source in the window's scan reported ok."""
        return all(r.status == "ok" for r in self.health)


def build_weekly_report(
    rows: Sequence[Mapping[str, object]],
    health: Sequence[SourceHealthRecord],
    tickers: Sequence[str],
    as_of: datetime,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    top_n: int = DEFAULT_TOP_MOVERS,
    pilot_ground_truth: set[str] | None = None,
    earnings_dates: Mapping[str, date] | None = None,
) -> WeeklyReport:
    """Aggregate one week of signal rows into a ``WeeklyReport``.

    ``as_of`` is the scan instant and the window's exclusive end; the current
    window is the ``window_days`` before it and the comparison window is the
    ``window_days`` before that. Deterministic: identical inputs yield an
    identical report, including the batch attestation anchor.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    window_end_dt = as_of
    window_start_dt = as_of - timedelta(days=window_days)
    prev_end_dt = window_start_dt
    prev_start_dt = as_of - timedelta(days=2 * window_days)
    current = (window_start_dt, window_end_dt)
    previous = (prev_start_dt, prev_end_dt)

    rows_in_window = [r for r in rows if in_window(parse_published_at(r), current)]

    category_counts: dict[str, int] = {}
    for row in rows_in_window:
        category = str(row.get("category") or "other")
        category_counts[category] = category_counts.get(category, 0) + 1

    return WeeklyReport(
        generated_at=as_of,
        window_start=window_start_dt.date(),
        window_end=window_end_dt.date(),
        total_signals=len(rows_in_window),
        queue_events=queue_events_per_iso(rows, current),
        theme_deltas=theme_heat_delta(rows, current, previous),
        movers=score_movers(rows, tickers, as_of, window_days=window_days, top_n=top_n),
        pilot=v4_pilot_metrics(rows, pilot_ground_truth),
        health=list(health),
        lag=earnings_lags(rows, earnings_dates or {}, current),
        batch_hash=batch_hash_for_rows(rows_in_window),
        categories=category_counts,
    )


def _fmt(value: float | None, pct: bool = False) -> str:
    if value is None:
        return "unaudited" if pct else "n/a"
    return f"{value:.0%}" if pct else f"{value:g}"


def _lag_line(lag: LagTracker) -> str:
    if lag.known_count == 0:
        detail = "no lags knowable yet — the baseline is collecting"
    else:
        detail = (
            f"median {lag.median_lag_days:g}d "
            f"(min {lag.min_lag_days}d, max {lag.max_lag_days}d, n={lag.known_count})"
        )
    return (
        f"{lag.signals_with_tickers} ticker-mapped signal(s); {detail}; "
        f"{lag.pending} ticker pair(s) pending a next earnings date"
    )


def render_markdown(report: WeeklyReport) -> str:
    """Render the weekly report as the committed public page.

    Free-tier display rules apply (design doc §6): no numeric scenario
    ranges; sources and invalidation conditions are the store's business and
    live on the signal rows, not in this aggregate.
    """
    lines: list[str] = []
    lines.append(f"# Weekly Signal Report — {report.window_start.isoformat()}")
    lines.append("")
    lines.append(
        f"Window: {report.window_start.isoformat()} → {report.window_end.isoformat()} "
        f"(generated {report.generated_at.strftime('%Y-%m-%dT%H:%M:%SZ')})."
    )
    lines.append("")

    lines.append("## At a glance")
    lines.append("")
    health_word = "healthy" if report.healthy else "DEGRADED — see source health"
    lines.append(
        f"- {report.total_signals} signal(s) in window "
        f"({', '.join(f'{k}: {v}' for k, v in sorted(report.categories.items())) or 'none'}); "
        f"sources: {health_word}."
    )
    lines.append(f"- Batch attestation anchor: `{report.batch_hash}`.")
    lines.append("")

    lines.append("## Queue events by ISO (V2)")
    lines.append("")
    if report.queue_events:
        lines.append("| ISO | New | Transitioned | Withdrawn |")
        lines.append("| --- | --- | --- | --- |")
        for e in report.queue_events:
            lines.append(f"| {e.iso} | {e.new_projects} | {e.transitions} | {e.withdrawn} |")
    else:
        lines.append("_No queue events in this window._")
    lines.append("")

    lines.append("## Theme heat (signals, week over week)")
    lines.append("")
    if report.theme_deltas:
        lines.append("| Theme | This week | Last week | Delta |")
        lines.append("| --- | --- | --- | --- |")
        for t in report.theme_deltas:
            sign = "+" if t.delta > 0 else ""
            lines.append(f"| {t.theme} | {t.current} | {t.previous} | {sign}{t.delta} |")
    else:
        lines.append("_No themed signals in this or the prior window._")
    lines.append("")

    lines.append(f"## Alpha Score movers (top {len(report.movers)})")
    lines.append("")
    if report.movers:
        lines.append("| Ticker | Score | Δ 7d |")
        lines.append("| --- | --- | --- |")
        for m in report.movers:
            sign = "+" if m.delta >= 0 else ""
            lines.append(f"| {m.ticker} | {m.score:.1f} | {sign}{m.delta:.1f} |")
    else:
        lines.append("_No scored movement in this window._")
    lines.append("")

    lines.append("## V4 permitting pilot")
    lines.append("")
    lines.append(
        f"- Detections: {report.pilot.detections}; audited ground truth: "
        f"{report.pilot.ground_truth or 'not yet audited this week'}."
    )
    lines.append(
        f"- Precision: {_fmt(report.pilot.precision, pct=True)} · "
        f"Recall: {_fmt(report.pilot.recall, pct=True)}"
        + (
            ""
            if report.pilot.precision is not None
            else " — pending the human audit of this week's detections"
        )
        + "."
    )
    lines.append("")

    lines.append("## Source health")
    lines.append("")
    if report.health:
        lines.append("| Source | Status | Detail |")
        lines.append("| --- | --- | --- |")
        for r in report.health:
            lines.append(f"| {r.source_name} | {r.status} | {r.detail} |")
    else:
        lines.append("_No source checks recorded._")
    lines.append("")

    lines.append("## Signal-to-earnings lag tracker")
    lines.append("")
    lines.append(f"- {_lag_line(report.lag)}")
    lines.append("")

    lines.append(
        "_Aggregates are computed from the append-only signal store; capacity "
        "figures appear only as de-weighted context. Not investment advice._"
    )
    return "\n".join(lines) + "\n"


def report_payload(report: WeeklyReport) -> dict[str, Any]:
    """JSON-safe payload for the Explorer's ``/weekly`` route queries.

    Numeric precision/recall are rounded to three decimals; ``None`` values
    mean "undefined", which the route renders as unaudited/pending — the
    distinction must survive serialization.
    """
    return {
        "generated_at": report.generated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_start": report.window_start.isoformat(),
        "window_end": report.window_end.isoformat(),
        "healthy": report.healthy,
        "total_signals": report.total_signals,
        "categories": dict(sorted(report.categories.items())),
        "batch_hash": report.batch_hash,
        "queue_events": [
            {
                "iso": e.iso,
                "new_projects": e.new_projects,
                "transitions": e.transitions,
                "withdrawn": e.withdrawn,
            }
            for e in report.queue_events
        ],
        "theme_deltas": [
            {
                "theme": t.theme,
                "current": t.current,
                "previous": t.previous,
                "delta": t.delta,
            }
            for t in report.theme_deltas
        ],
        "movers": [
            {
                "ticker": m.ticker,
                "score": round(m.score, 1),
                "delta": round(m.delta, 1),
            }
            for m in report.movers
        ],
        "pilot": {
            "detections": report.pilot.detections,
            "true_positives": report.pilot.true_positives,
            "false_positives": report.pilot.false_positives,
            "ground_truth": report.pilot.ground_truth,
            "precision": (
                round(report.pilot.precision, 3) if report.pilot.precision is not None else None
            ),
            "recall": (round(report.pilot.recall, 3) if report.pilot.recall is not None else None),
        },
        "source_health": [
            {
                "source_name": r.source_name,
                "status": r.status,
                "detail": r.detail,
            }
            for r in report.health
        ],
        "lag": {
            "signals_with_tickers": report.lag.signals_with_tickers,
            "known_count": report.lag.known_count,
            "min_lag_days": report.lag.min_lag_days,
            "median_lag_days": report.lag.median_lag_days,
            "max_lag_days": report.lag.max_lag_days,
            "pending": report.lag.pending,
        },
        "category_names": {"V2": V2_CATEGORY, "V4": V4_CATEGORY},
    }


def write_outputs(report: WeeklyReport, out_dir: Path | str) -> tuple[Path, Path]:
    """Write ``report.md`` (public page) and ``report.json`` (queryable) — deterministically."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    md_path = out / "report.md"
    json_path = out / "report.json"
    md_path.write_text(render_markdown(report), encoding="utf-8")
    json_path.write_text(
        json.dumps(report_payload(report), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return md_path, json_path


# --- CLI ---------------------------------------------------------------------


def load_earnings_csv(path: Path | str) -> dict[str, date]:
    """Load an optional earnings calendar (ticker, earnings_date — ISO dates).

    The calendar is config, not code: a vendor is an open MVP question (build
    spec, "Open questions"), so the file is optional and the tracker reports
    pairs without a date as pending.
    """
    import csv

    earnings: dict[str, date] = {}
    with Path(path).open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        missing = [c for c in ("ticker", "earnings_date") if c not in header]
        if missing:
            raise ValueError(f"earnings calendar missing column(s): {missing}")
        for i, row in enumerate(reader, start=2):
            ticker = (row.get("ticker") or "").strip()
            raw_date = (row.get("earnings_date") or "").strip()
            if not ticker or not raw_date:
                raise ValueError(f"earnings calendar line {i}: empty field(s)")
            if ticker in earnings:
                raise ValueError(f"earnings calendar line {i}: duplicate ticker {ticker!r}")
            earnings[ticker] = datetime.strptime(raw_date, "%Y-%m-%d").date()
    return earnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pipelines.report.weekly",
        description="Build the weekly aggregate report (markdown page + queryable JSON).",
    )
    parser.add_argument("--signals", type=Path, required=True, help="signal rows JSON")
    parser.add_argument("--health", type=Path, required=True, help="source-health records JSON")
    parser.add_argument(
        "--universe-csv",
        type=Path,
        default=None,
        help="coverage universe (default: the committed universe.csv) — movers scope",
    )
    parser.add_argument(
        "--earnings",
        type=Path,
        default=None,
        help="optional earnings calendar CSV (ticker, earnings_date) for the lag tracker",
    )
    parser.add_argument(
        "--pilot-ground-truth",
        type=Path,
        default=None,
        help="optional JSON array of audited true permit-announcement URLs",
    )
    parser.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    parser.add_argument(
        "--as-of",
        type=str,
        default=None,
        help="report instant (ISO, default now UTC) — pin it for deterministic runs",
    )
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    args = parser.parse_args(argv)

    now = (
        datetime.fromisoformat(args.as_of.strip().replace("Z", "+00:00"))
        if args.as_of
        else datetime.now(UTC)
    )
    if now.tzinfo is None:
        parser.error("--as-of must be timezone-aware")

    rows: list[dict[str, Any]] = json.loads(args.signals.read_text(encoding="utf-8"))
    health = [
        SourceHealthRecord(
            source_name=record["source_name"],
            status=record["status"],
            detail=record["detail"],
        )
        for record in json.loads(args.health.read_text(encoding="utf-8"))
    ]

    # The `data` package (pipelines/data/) ships beside the package, not in the
    # wheel — same sys.path treatment tests get from conftest.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from data.universe import UNIVERSE_CSV, load_universe

    universe = load_universe(args.universe_csv or UNIVERSE_CSV)
    earnings_dates = load_earnings_csv(args.earnings) if args.earnings else {}
    ground_truth: set[str] | None = None
    if args.pilot_ground_truth:
        ground_truth = set(json.loads(args.pilot_ground_truth.read_text(encoding="utf-8")))

    report = build_weekly_report(
        rows,
        health,
        [entry.ticker for entry in universe],
        now,
        window_days=args.window_days,
        pilot_ground_truth=ground_truth,
        earnings_dates=earnings_dates,
    )
    md_path, json_path = write_outputs(report, args.out)
    health_word = "healthy" if report.healthy else "DEGRADED"
    print(
        f"weekly report written: {md_path} and {json_path} "
        f"({report.total_signals} signal(s), sources {health_word})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
