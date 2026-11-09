"""Weekly report tests: fixture DB → aggregate correctness, red-on-stale, stable hash.

The "fixture DB" is a list of canonical signal rows (the publish contract's
dicts plus published_at) — the same shape the store persists and the weekly
workflow passes to the report. Aggregates, pilot metrics, the lag tracker,
batch-hash stability, and markdown/JSON determinism all run against it with
no DB, no network, no clock reads.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from pipelines.ingest.source_health import (
    SourceHealthError,
    SourceHealthRecord,
    assert_scan_healthy,
    evaluate_freshness,
    record_failure,
)
from pipelines.report import (
    WeeklyReport,
    build_weekly_report,
    earnings_lags,
    parse_published_at,
    queue_events_per_iso,
    render_markdown,
    report_payload,
    score_movers,
    theme_heat_delta,
    v4_pilot_metrics,
)
from pipelines.scoring.alpha_score import SignalEvent, score_companies

NOW = datetime(2026, 11, 9, 6, 0, tzinfo=UTC)
TICKERS = ("GEV", "PWR", "VRT", "EQIX")


def row(
    signal_id: str,
    *,
    category: str = "V2_INTERCONNECTION",
    event_type: str = "status_transition",
    tickers: tuple[str, ...] = (),
    theme: str | None = "Grid & Transmission",
    iso: str | None = "PJM",
    direction: str = "positive",
    strength: int = 3,
    published_at: str = "2026-11-05T06:00:00Z",
    sources: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return {
        "signal_id": signal_id,
        "category": category,
        "event_type": event_type,
        "tickers": list(tickers),
        "theme": theme,
        "iso": iso,
        "direction": direction,
        "strength": strength,
        "published_at": published_at,
        "sources": sources
        if sources is not None
        else [{"type": "ISO_queue", "url": "https://example.com"}],
    }


def fixture_db() -> list[dict[str, object]]:
    """One week of events across both proprietary pipelines."""
    return [
        # PJM: two transitions, one withdrawal; ERCOT: one new project.
        row("SIG-1", iso="PJM", event_type="status_transition", tickers=("GEV",)),
        row("SIG-2", iso="PJM", event_type="status_transition", tickers=("PWR",), strength=4),
        row(
            "SIG-3",
            iso="PJM",
            event_type="withdrawal",
            tickers=(),
            direction="negative",
            published_at="2026-11-07T06:00:00Z",
        ),
        row("SIG-4", iso="ERCOT", event_type="new_project", theme="Data Centers"),
        # CAISO: a capacity change (de-weighted context, not a transition).
        row("SIG-5", iso="CAISO", event_type="capacity_change", theme="AI Power"),
        # Outside the window: last week's heat for the theme-delta comparison.
        row("SIG-6", iso="PJM", published_at="2026-11-01T06:00:00Z", theme="AI Power"),
        row("SIG-7", iso="ERCOT", published_at="2026-11-01T12:00:00Z", theme="AI Power"),
        # V4 legs: two county detections (one true, one false in the audit) + EDGAR.
        row(
            "SIG-8",
            category="V4_DATA_CENTER_BUILDOUT",
            event_type="new_project",
            tickers=("EQIX",),
            theme="Data Centers",
            sources=[{"type": "COUNTY_PERMIT", "url": "https://permits/loudoun-true"}],
        ),
        row(
            "SIG-9",
            category="V4_DATA_CENTER_BUILDOUT",
            event_type="new_project",
            theme="Data Centers",
            sources=[{"type": "COUNTY_PERMIT", "url": "https://permits/false-positive"}],
        ),
        row(
            "SIG-10",
            category="V4_DATA_CENTER_BUILDOUT",
            event_type="new_project",
            tickers=("EQIX",),
            theme="Data Centers",
            sources=[{"type": "EDGAR_FTS", "url": "https://sec.gov/dlr-8k"}],
        ),
    ]


def health(ok: bool = True) -> list[SourceHealthRecord]:
    if ok:
        return [
            SourceHealthRecord("queue-pjm", "ok", "last success 0d ago"),
            SourceHealthRecord("edgar-fts", "ok", "3 hit(s)"),
        ]
    return [
        SourceHealthRecord("queue-pjm", "ok", "last success 0d ago"),
        SourceHealthRecord("queue-ercot", "stale", "last success 12d ago exceeds 7d budget"),
    ]


def build(
    rows: list[dict[str, object]] | None = None,
    health_records: list[SourceHealthRecord] | None = None,
    **overrides: object,
) -> WeeklyReport:
    return build_weekly_report(
        fixture_db() if rows is None else rows,
        health() if health_records is None else health_records,
        list(TICKERS),
        NOW,
        **overrides,  # type: ignore[arg-type]
    )


# --- Aggregates from the fixture DB -----------------------------------------


def test_queue_events_per_iso_counts_only_v2_events_in_window() -> None:
    events = {e.iso: e for e in queue_events_per_iso(fixture_db(), (NOW.replace(day=2), NOW))}
    # Half-open window handling: SIG-6 (Nov 1) is out, everything Nov 2+ is in.
    assert events["PJM"].new_projects == 0
    assert events["PJM"].transitions == 2
    assert events["PJM"].withdrawn == 1
    assert events["ERCOT"].new_projects == 1
    # Capacity change is de-weighted context — never counted as a transition,
    # and an ISO with only context rows is absent from the event table
    # (aliveness is the health table's job, not the event counts').
    assert "CAISO" not in events


def test_theme_heat_delta_compares_adjacent_weeks() -> None:
    window = (NOW.replace(day=2), NOW)
    prev = (NOW.replace(day=2).replace(month=10), NOW.replace(day=2))
    deltas = {d.theme: d for d in theme_heat_delta(fixture_db(), window, prev)}
    # Grid & Transmission: 3 this week (SIG-1/2/3), 0 last week.
    assert deltas["Grid & Transmission"].current == 3
    assert deltas["Grid & Transmission"].delta == 3
    # AI Power: only the de-weighted CAISO context row this week (SIG-5),
    # two last week — heat counts rows, it does not grade event types.
    assert deltas["AI Power"].current == 1
    assert deltas["AI Power"].delta == -1
    # Data Centers: 4 rows this week — three V4 legs (SIG-8/9/10) plus the
    # ERCOT new-project row (SIG-4), which carries the same theme.
    assert deltas["Data Centers"].current == 4


def test_score_movers_rank_and_compute_from_the_scoring_engine() -> None:
    movers = score_movers(fixture_db(), list(TICKERS), NOW, window_days=7, top_n=2)
    assert len(movers) == 2
    # Movers are ranked best-first.
    assert movers[0].delta >= movers[1].delta
    # Cross-check the leader against the engine directly.
    events = [
        SignalEvent.from_row(dict(r, published_at=parse_published_at(r))) for r in fixture_db()
    ]
    scores = score_companies(events, list(TICKERS), NOW)
    assert movers[0].score == pytest.approx(scores[movers[0].ticker].score)


def test_v4_pilot_metrics_precision_recall_against_audit() -> None:
    ground_truth = {"https://permits/loudoun-true", "https://permits/missed-detection"}
    metrics = v4_pilot_metrics(fixture_db(), ground_truth)
    # EDGAR rows are not county-leg detections.
    assert metrics.detections == 2
    assert metrics.true_positives == 1
    assert metrics.false_positives == 1
    assert metrics.ground_truth == 2
    assert metrics.precision == pytest.approx(0.5)
    assert metrics.recall == pytest.approx(0.5)


def test_v4_pilot_without_audit_is_unaudited_not_zero() -> None:
    metrics = v4_pilot_metrics(fixture_db(), None)
    assert metrics.detections == 2
    assert metrics.precision is None
    assert metrics.recall is None


def test_earnings_lags_track_known_and_pending_pairs() -> None:
    window = (NOW.replace(day=2), NOW)
    calendar = {"GEV": date(2026, 11, 12), "PWR": date(2026, 10, 1)}  # PWR's date passed
    tracker = earnings_lags(fixture_db(), calendar, window)
    assert tracker.signals_with_tickers == 4  # SIG-1, SIG-2, SIG-8, SIG-10 have tickers
    # GEV on SIG-1: Nov 5 → Nov 12 = 7 days. PWR's date is past → pending.
    assert tracker.known_lags == (7,)
    assert tracker.pending == 3  # SIG-2/PWR past + SIG-8/EQIX + SIG-10/EQIX unmapped
    assert tracker.median_lag_days == 7.0


# --- Stale source → failed check (red), never silent zero --------------------


def test_stale_source_fails_the_scan_gate() -> None:
    records = [
        evaluate_freshness("queue-pjm", NOW, NOW),  # fresh — ok
        evaluate_freshness("queue-ercot", NOW.replace(day=1), NOW, max_age_days=7),
    ]
    assert records[1].status == "stale"
    with pytest.raises(SourceHealthError):
        assert_scan_healthy(records)


def test_failed_source_fails_the_scan_gate() -> None:
    with pytest.raises(SourceHealthError):
        assert_scan_healthy([record_failure("queue-miso", "live fetch failed: HTTP 404")])


def test_report_flags_degraded_health_and_renders_it() -> None:
    report = build(health_records=health(ok=False))
    assert report.healthy is False
    markdown = render_markdown(report)
    assert "DEGRADED" in markdown
    assert "queue-ercot" in markdown
    assert "stale" in markdown


# --- Batch hash stability ----------------------------------------------------


def test_batch_hash_stable_for_identical_input_regardless_of_order() -> None:
    report_a = build()
    shuffled = list(reversed(fixture_db()))
    report_b = build(rows=shuffled)
    assert report_a.batch_hash == report_b.batch_hash
    # Recomputing the same report is byte-stable too.
    assert build().batch_hash == report_a.batch_hash


def test_batch_hash_changes_when_the_window_content_changes() -> None:
    report_a = build()
    rows = fixture_db()
    rows.append(row("SIG-11", published_at="2026-11-08T06:00:00Z"))
    assert build(rows=rows).batch_hash != report_a.batch_hash


# --- Determinism of both outputs ---------------------------------------------


def test_markdown_and_json_outputs_are_deterministic() -> None:
    first = build()
    second = build()
    assert render_markdown(first) == render_markdown(second)
    payload_a = report_payload(first)
    payload_b = report_payload(second)
    assert payload_a == payload_b
    assert payload_a["healthy"] is True
    assert payload_a["batch_hash"] == first.batch_hash
    assert payload_a["pilot"]["precision"] is None  # unaudited survives serialization


def test_report_window_counts_only_window_rows() -> None:
    report = build()
    # 8 rows in window (SIG-1..5, 8, 9, 10); SIG-6/7 are last week's.
    assert report.total_signals == 8
    assert report.window_end == NOW.date()
    assert report.categories["V2_INTERCONNECTION"] == 5
    assert report.categories["V4_DATA_CENTER_BUILDOUT"] == 3
