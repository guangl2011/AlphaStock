"""Pure weekly aggregates over canonical signal rows.

Every function here takes rows shaped like the ``signals`` table (the same
dicts ``publish_with_attestation`` consumes, plus a ``published_at`` value)
and returns plain dataclasses — no DB, no clock reads, no I/O. The weekly
report and its tests feed the same fixture DB through the same functions, so
"aggregate correctness" is one code path, not a reimplementation.

The signal-to-earnings lag tracker and the V4 pilot metrics are the MVP's
baseline collectors (build spec, "Load-bearing assumption"): they record what
is knowable today and say so explicitly when ground truth is missing — an
unaudited pilot and a pending earnings date are reported as such, never as
zeros.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from pipelines.attestation.hashing import batch_hash
from pipelines.scoring.alpha_score import ScoreDelta, SignalEvent, score_deltas

#: Half-open window [start, end) — a row at exactly `end` belongs to the next window.
Window = tuple[datetime, datetime]

#: Build-spec category names.
V2_CATEGORY = "V2_INTERCONNECTION"
V4_CATEGORY = "V4_DATA_CENTER_BUILDOUT"

_COUNTY_SOURCE_TYPE = "COUNTY_PERMIT"


def parse_published_at(row: Mapping[str, object]) -> datetime:
    """Parse a row's ``published_at`` (ISO string or datetime) — tz-aware or error.

    Rows persisted as JSON carry ISO strings; rows straight from the store
    carry datetimes. A naive timestamp would make window comparisons
    order-dependent, so it is rejected loudly.
    """
    raw = row.get("published_at")
    if isinstance(raw, datetime):
        parsed = raw
    elif isinstance(raw, str) and raw.strip():
        text = raw.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"published_at {raw!r} is not an ISO timestamp") from exc
    else:
        raise ValueError(f"row {row.get('signal_id', '?')!r} has no published_at")
    if parsed.tzinfo is None:
        raise ValueError(f"row {row.get('signal_id', '?')!r}: published_at must be timezone-aware")
    return parsed


def in_window(moment: datetime, window: Window) -> bool:
    """Week membership: start <= moment <= end.

    The end is inclusive — a signal published exactly at the report instant
    (e.g. the scan and report stages pinned to the same ``--as-of``) belongs
    to the week it closes, not to no week at all.
    """
    start, end = window
    return start <= moment <= end


def batch_hash_for_rows(rows: Sequence[Mapping[str, object]]) -> str:
    """Batch attestation anchor for the report's signal set.

    Delegates to the attestation module's ``batch_hash`` — order-independent
    and stable for identical input, so the weekly anchor is reproducible.
    """
    return batch_hash(list(rows))


# --- Queue events per ISO -------------------------------------------------


@dataclass(frozen=True)
class IsoQueueEvents:
    """V2 event counts for one ISO over one window."""

    iso: str
    new_projects: int
    transitions: int
    withdrawn: int


_QUEUE_EVENT_FIELDS = {
    "new_project": "new_projects",
    "status_transition": "transitions",
    "withdrawal": "withdrawn",
}


def queue_events_per_iso(
    rows: Iterable[Mapping[str, object]], window: Window
) -> list[IsoQueueEvents]:
    """New/transitioned/withdrawn counts per ISO, ISO name sorted.

    Capacity-change events are de-weighted context (locked decision 3) and
    are not counted as transitions.
    """
    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        if row.get("category") != V2_CATEGORY:
            continue
        field = _QUEUE_EVENT_FIELDS.get(str(row.get("event_type")))
        if field is None:
            continue
        if not in_window(parse_published_at(row), window):
            continue
        iso = str(row.get("iso") or "").strip()
        if not iso:
            raise ValueError(f"row {row.get('signal_id', '?')!r}: V2 signal with no iso")
        per_iso = counts.setdefault(iso, {"new_projects": 0, "transitions": 0, "withdrawn": 0})
        per_iso[field] += 1
    return [IsoQueueEvents(iso=iso, **per_iso) for iso, per_iso in sorted(counts.items())]


# --- Theme heat deltas ------------------------------------------------------


@dataclass(frozen=True)
class ThemeDelta:
    """Signal count for one theme this window vs the previous one."""

    theme: str
    current: int
    previous: int
    delta: int


def theme_heat_delta(
    rows: Iterable[Mapping[str, object]], current: Window, previous: Window
) -> list[ThemeDelta]:
    """Theme heat (signal counts) delta, best-first with ties by theme name.

    Rows without a theme (unmapped yet) are skipped — theme assignment is a
    pipeline stage's job, and a missing theme must not distort a theme it
    does not belong to.
    """
    cur_heat: dict[str, int] = {}
    prev_heat: dict[str, int] = {}
    for row in rows:
        theme = str(row.get("theme") or "").strip()
        if not theme:
            continue
        published = parse_published_at(row)
        if in_window(published, current):
            cur_heat[theme] = cur_heat.get(theme, 0) + 1
        elif in_window(published, previous):
            prev_heat[theme] = prev_heat.get(theme, 0) + 1
    themes = sorted(set(cur_heat) | set(prev_heat))
    return [
        ThemeDelta(
            theme=t,
            current=cur_heat.get(t, 0),
            previous=prev_heat.get(t, 0),
            delta=cur_heat.get(t, 0) - prev_heat.get(t, 0),
        )
        for t in sorted(themes, key=lambda t: (-(cur_heat.get(t, 0)), t))
    ]


# --- Alpha Score movers -----------------------------------------------------


def score_movers(
    rows: Iterable[Mapping[str, object]],
    tickers: Sequence[str],
    as_of: datetime,
    *,
    window_days: int = 7,
    top_n: int = 10,
) -> list[ScoreDelta]:
    """Alpha Score change over the window, ranked best-first, top ``n``.

    Reuses the scoring engine's ``score_deltas`` — the report never
    reimplements the score. Ties already break by ticker there.
    """
    events = []
    for row in rows:
        prepared = dict(row)
        prepared["published_at"] = parse_published_at(row)
        events.append(SignalEvent.from_row(prepared))
    return score_deltas(events, tickers, as_of, window_days)[:top_n]


# --- V4 pilot precision / recall --------------------------------------------


@dataclass(frozen=True)
class PilotMetrics:
    """County-permitting pilot performance against audited ground truth.

    ``precision``/``recall`` are ``None`` when undefined (no audit labels, or
    an empty denominator) — an unaudited pilot is reported as unaudited.
    """

    detections: int
    true_positives: int
    false_positives: int
    ground_truth: int
    precision: float | None
    recall: float | None


def pilot_detection_keys(rows: Iterable[Mapping[str, object]]) -> set[str]:
    """County-leg detection identity: the source URL of each V4 permit signal."""
    keys: set[str] = set()
    for row in rows:
        if row.get("category") != V4_CATEGORY:
            continue
        for source in row.get("sources") or ():
            if (
                isinstance(source, Mapping)
                and source.get("type") == _COUNTY_SOURCE_TYPE
                and source.get("url")
            ):
                keys.add(str(source["url"]))
    return keys


def v4_pilot_metrics(
    rows: Iterable[Mapping[str, object]],
    ground_truth_urls: set[str] | None,
) -> PilotMetrics:
    """Pilot precision/recall against a human-audited ground-truth URL set.

    ``ground_truth_urls=None`` means the week's detections have not been
    audited — metrics are undefined, not zero.
    """
    detected = pilot_detection_keys(rows)
    if ground_truth_urls is None:
        return PilotMetrics(
            detections=len(detected),
            true_positives=0,
            false_positives=0,
            ground_truth=0,
            precision=None,
            recall=None,
        )
    tp = len(detected & ground_truth_urls)
    fp = len(detected - ground_truth_urls)
    fn = len(ground_truth_urls - detected)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return PilotMetrics(
        detections=len(detected),
        true_positives=tp,
        false_positives=fp,
        ground_truth=len(ground_truth_urls),
        precision=precision,
        recall=recall,
    )


# --- Signal-to-earnings lag tracker ------------------------------------------


@dataclass(frozen=True)
class LagTracker:
    """Signal-to-earnings lag baseline for one window.

    The lag is the days from a signal's publication to the mapped ticker's
    next earnings announcement on/after publication — the number the
    retrospective backtest needs. Signals whose tickers have no known
    next-earnings date are ``pending``, never silently dropped.
    """

    signals_with_tickers: int
    known_lags: tuple[int, ...]
    pending: int

    @property
    def known_count(self) -> int:
        return len(self.known_lags)

    @property
    def min_lag_days(self) -> int | None:
        return min(self.known_lags) if self.known_lags else None

    @property
    def max_lag_days(self) -> int | None:
        return max(self.known_lags) if self.known_lags else None

    @property
    def median_lag_days(self) -> float | None:
        if not self.known_lags:
            return None
        ordered = sorted(self.known_lags)
        mid = len(ordered) // 2
        if len(ordered) % 2:
            return float(ordered[mid])
        return (ordered[mid - 1] + ordered[mid]) / 2.0


def earnings_lags(
    rows: Iterable[Mapping[str, object]],
    earnings_dates: Mapping[str, date],
    window: Window,
) -> LagTracker:
    """Track publication→next-earnings lags for ticker-mapped signals in window.

    Each (signal, ticker) pair contributes at most one lag. A pair with no
    known earnings date, or one already past at publication, counts as
    pending — the next quarter's date is not yet knowable.
    """
    known: list[int] = []
    signals_with_tickers = 0
    pending = 0
    for row in rows:
        published = parse_published_at(row)
        if not in_window(published, window):
            continue
        tickers = row.get("tickers") or ()
        if not tickers:
            continue
        signals_with_tickers += 1
        pub_date = published.date()
        for ticker in tickers:
            next_earnings = earnings_dates.get(str(ticker))
            if next_earnings is None or next_earnings < pub_date:
                pending += 1
            else:
                known.append((next_earnings - pub_date).days)
    return LagTracker(
        signals_with_tickers=signals_with_tickers,
        known_lags=tuple(known),
        pending=pending,
    )
