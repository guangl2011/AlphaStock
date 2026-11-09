"""Weekly report: aggregates over the signal store, rendered for /weekly.

Public API: :func:`build_weekly_report` composes the pure aggregates;
:func:`render_markdown` / :func:`report_payload` / :func:`write_outputs`
render the committed public page and the queryable JSON for the Explorer's
``/weekly`` route.
"""

from __future__ import annotations

from .aggregates import (
    IsoQueueEvents,
    LagTracker,
    PilotMetrics,
    ThemeDelta,
    Window,
    batch_hash_for_rows,
    earnings_lags,
    in_window,
    parse_published_at,
    pilot_detection_keys,
    queue_events_per_iso,
    score_movers,
    theme_heat_delta,
    v4_pilot_metrics,
)
from .weekly import (
    DEFAULT_TOP_MOVERS,
    DEFAULT_WINDOW_DAYS,
    WeeklyReport,
    build_weekly_report,
    render_markdown,
    report_payload,
    write_outputs,
)

__all__ = [
    "DEFAULT_TOP_MOVERS",
    "DEFAULT_WINDOW_DAYS",
    "IsoQueueEvents",
    "LagTracker",
    "PilotMetrics",
    "ThemeDelta",
    "WeeklyReport",
    "Window",
    "batch_hash_for_rows",
    "build_weekly_report",
    "earnings_lags",
    "in_window",
    "parse_published_at",
    "pilot_detection_keys",
    "queue_events_per_iso",
    "render_markdown",
    "report_payload",
    "score_movers",
    "theme_heat_delta",
    "v4_pilot_metrics",
    "write_outputs",
]
