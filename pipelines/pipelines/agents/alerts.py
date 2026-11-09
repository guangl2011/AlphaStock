"""Weekly top-10 alerts: select, draft, verify, record, stage (never send).

Spec (build spec art_jLZaGljO, "Coverage universe and weekly alerts"):
every Monday the top 10 covered companies by current Alpha Score get a
bilingual alert brief — score, delta vs. last week, the new signals driving
the move, a qualitative scenario, and invalidation conditions. A non-empty
``pipelines/data/watchlist_top10.csv`` overrides the ranking entirely (the
owner pins names); header-only means rank by score.

Every alert routes through the same Verification Agent as Daily Alpha
(:func:`pipelines.agents.verifier.verify_draft`); an alert that fails is
dropped — logged with machine-readable reasons, never recorded, never sent.
Passing alerts are recorded in ``alert_history`` (approved=false,
sent_at=null) and the whole run is staged as one GitHub issue for the
human approval gate (design doc §8.1). There is deliberately no email/Slack
path anywhere in this module: nothing is delivered unapproved.

Signals come from the merged weekly-scan pipeline — its published store rows
(Supabase REST, reusing ``daily_alpha.fetch_signals_supabase``) on live runs,
a committed sample batch in fixture mode.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from pipelines.agents.daily_alpha import fetch_signals_supabase
from pipelines.agents.verifier import (
    MAX_BRIEF_ITEMS,
    HttpSourceChecker,
    SourceChecker,
    SourceRef,
    VerifiableDraft,
    VerifiableItem,
    VerificationIssue,
    verify_draft,
    verify_empty_run,
)
from pipelines.agents.writer import (
    HISTORY_DAYS,
    STATUS_EMPTY_RUN,
    STATUS_PENDING_APPROVAL,
    STATUS_REJECTED,
    SignalRow,
    WriterInputError,
    normalize_row,
)
from pipelines.scoring import ScoreDelta, SignalEvent, score_deltas

#: Score-delta window — "delta vs last week" (the /alerts route renders it
#: as "+X vs prior week"). Driving signals share the window.
DELTA_WINDOW_DAYS = 7

#: The run stages exactly this many alerts (build spec: weekly alerts for
#: "10 selected stocks" — top 10 by Alpha Score or the curated override).
ALERT_COUNT = 10

#: Cap on the curated override list: the file is named ``watchlist_top10.csv``.
WATCHLIST_CAP = 10

#: Idempotent re-runs must not clobber a row the owner has approved.
_ALERT_ID_PREFIX = "alert"

_APPROVAL_BANNER = (
    "> ⛔ **Human approval gate (design doc §8.1):** these alerts are staged for review only. "
    "Nothing is emailed or published automatically — a research owner must approve on this "
    "issue before any send. There is no send path in this MVP workflow."
)

_INVALIDATION_EN = (
    "This alert is withdrawn if any cited signal is superseded or its invalidation "
    "condition triggers. Research content, not investment advice."
)
_INVALIDATION_ZH = "若任一所引信号被取代或其失效条件触发，本警报即告失效。研究内容，非投资建议。"

__all__ = [
    "ALERT_COUNT",
    "DELTA_WINDOW_DAYS",
    "STATUS_EMPTY_RUN",
    "STATUS_PENDING_APPROVAL",
    "STATUS_REJECTED",
    "AlertDraft",
    "AlertHistoryRow",
    "AlertInputError",
    "AlertRun",
    "AlertSelection",
    "CompanyProfile",
    "StagedAlert",
    "StagedAlertRun",
    "WatchlistEntry",
    "alert_issue_title",
    "build_alerts",
    "driving_signals",
    "load_top10_watchlist",
    "publish_alert_history",
    "select_top10",
    "stage_alerts",
]


class AlertInputError(ValueError):
    """An input (signal row, watchlist, selection) violates its contract."""


class CompanyProfile(Protocol):
    """Structural view of ``data.universe.UniverseEntry`` (kept import-free).

    The ``data`` package ships beside the pipelines package, not inside the
    wheel — same reason ``report.weekly`` imports it inside the CLI only.
    """

    ticker: str
    company: str
    category: str
    themes: str
    exposure_type: str
    thesis: str
    key_risk: str
    source_url: str


# --- Curated override list ----------------------------------------------------


@dataclass(frozen=True)
class WatchlistEntry:
    """One row of ``pipelines/data/watchlist_top10.csv`` (owner-pinned names)."""

    ticker: str
    rank: int
    rationale: str


def load_top10_watchlist(path: Path | str) -> list[WatchlistEntry]:
    """Load the curated override list; an empty (header-only) file means none.

    Loud on the contract breaks: missing columns, blank tickers, duplicate
    tickers, non-integer ranks, blank rationales, or more than ten rows.
    """
    path = Path(path)
    if not path.is_file():
        raise AlertInputError(f"watchlist file not found: {path}")
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        missing = [c for c in ("ticker", "rank", "rationale") if c not in header]
        if missing:
            raise AlertInputError(f"watchlist missing column(s): {missing}")
        entries: list[WatchlistEntry] = []
        seen: set[str] = set()
        for i, row in enumerate(reader, start=2):
            ticker = (row.get("ticker") or "").strip()
            rank_raw = (row.get("rank") or "").strip()
            rationale = (row.get("rationale") or "").strip()
            if not ticker:
                raise AlertInputError(f"watchlist line {i}: empty ticker")
            if ticker in seen:
                raise AlertInputError(f"watchlist line {i}: duplicate ticker {ticker!r}")
            seen.add(ticker)
            try:
                rank = int(rank_raw)
            except ValueError as exc:
                raise AlertInputError(
                    f"watchlist line {i}: rank {rank_raw!r} is not an integer"
                ) from exc
            if not rationale:
                raise AlertInputError(f"watchlist line {i}: empty rationale")
            entries.append(WatchlistEntry(ticker=ticker, rank=rank, rationale=rationale))
    if len(entries) > WATCHLIST_CAP:
        raise AlertInputError(
            f"watchlist carries {len(entries)} rows — the curated override caps at {WATCHLIST_CAP}"
        )
    return entries


# --- Selection ---------------------------------------------------------------


@dataclass(frozen=True)
class AlertSelection:
    """One selected company with the score facts its alert will carry."""

    ticker: str
    company: str
    entry: CompanyProfile
    score: float
    delta: float
    source: str  # "ranking" | "watchlist" — why this name was selected
    rank: int  # 1-based position in the final staged order


def _events(rows: Sequence[Mapping[str, Any]]) -> list[SignalEvent]:
    return [normalize_row(row).to_event() for row in rows]


def select_top10(
    rows: Sequence[Mapping[str, Any]],
    universe: Sequence[CompanyProfile],
    as_of: datetime,
    watchlist: Sequence[WatchlistEntry] = (),
) -> list[AlertSelection]:
    """Pick the alert list: curated override when non-empty, else top 10 by score.

    Ranking is by current Alpha Score (desc), ties broken by ticker for a
    stable order. The override list wins entirely — including when it pins
    fewer than ten names; each pinned ticker must be covered by the universe
    (the ticker-mapping evidence rule), else the run fails loudly.
    """
    by_ticker = {entry.ticker: entry for entry in universe}
    if not by_ticker:
        raise AlertInputError("the coverage universe is empty — nothing to select from")

    events = _events(rows)
    deltas = score_deltas(events, list(by_ticker), as_of, window_days=DELTA_WINDOW_DAYS)
    scored = {d.ticker: d for d in deltas}

    def selection(ticker: str, source: str, rank: int) -> AlertSelection:
        delta: ScoreDelta = scored[ticker]
        return AlertSelection(
            ticker=ticker,
            company=by_ticker[ticker].company,
            entry=by_ticker[ticker],
            score=delta.score,
            delta=delta.delta,
            source=source,
            rank=rank,
        )

    if watchlist:
        ordered = sorted(watchlist, key=lambda w: (w.rank, w.ticker))
        for entry in ordered:
            if entry.ticker not in by_ticker:
                raise AlertInputError(
                    f"watchlist ticker {entry.ticker!r} is not in the coverage universe "
                    "(ticker mappings must carry evidence — see build spec)"
                )
        return [selection(e.ticker, "watchlist", i) for i, e in enumerate(ordered, start=1)]

    ranked = sorted(by_ticker, key=lambda t: (-scored[t].score, t))
    return [selection(t, "ranking", i) for i, t in enumerate(ranked[:ALERT_COUNT], start=1)]


# --- Brief building ------------------------------------------------------------


def driving_signals(
    rows: Sequence[Mapping[str, Any]],
    ticker: str,
    as_of: datetime,
    *,
    window_days: int = DELTA_WINDOW_DAYS,
    max_items: int = MAX_BRIEF_ITEMS,
) -> list[SignalRow]:
    """The week's signals for one ticker: strength-desc, recency-desc, capped.

    These are the verifier's items — the alert's claims, each with its own
    sources and invalidation. Zero signals is not an error here; the alert
    simply cannot pass verification (no source-linked claims) and is dropped.
    """
    window_start = as_of - timedelta(days=window_days)
    candidates = [
        row
        for row in (normalize_row(r) for r in rows)
        if ticker in row.tickers and window_start < row.published_at <= as_of
    ]
    candidates.sort(key=lambda r: (-r.strength, -r.published_at.timestamp(), r.signal_id))
    return candidates[:max_items]


@dataclass(frozen=True)
class AlertDraft:
    """One company's bilingual alert brief, plus the score facts behind it."""

    as_of: datetime
    selection: AlertSelection
    items: tuple[SignalRow, ...]
    scenario_en: str
    scenario_zh: str
    invalidation_en: str
    invalidation_zh: str

    @property
    def title_en(self) -> str:
        return (
            f"Weekly Alert · {self.selection.ticker} — Alpha Score "
            f"{self.selection.score:.1f} ({self.selection.delta:+.1f} vs prior week)"
        )

    @property
    def title_zh(self) -> str:
        return (
            f"每周警报 · {self.selection.ticker} — Alpha Score "
            f"{self.selection.score:.1f}（较上周 {self.selection.delta:+.1f}）"
        )

    @property
    def alert_id(self) -> str:
        """Deterministic PK — re-runs on the same date are idempotent."""
        return f"{_ALERT_ID_PREFIX}-{self.as_of.strftime('%Y-%m-%d')}-{self.selection.ticker}"

    def verification_input(self) -> VerifiableDraft:
        fields = {
            "title_en": self.title_en,
            "title_zh": self.title_zh,
            "scenario_en": self.scenario_en,
            "scenario_zh": self.scenario_zh,
            "invalidation_en": self.invalidation_en,
            "invalidation_zh": self.invalidation_zh,
        }
        items = tuple(
            VerifiableItem(
                label=row.signal_id,
                summary_en=row.summary_en,
                summary_zh=row.summary_zh,
                invalidation=row.invalidation,
                sources=tuple(
                    SourceRef(url=str(s.get("url", "")), type=str(s.get("type", "unknown")))
                    for s in row.sources
                ),
            )
            for row in self.items
        )
        return VerifiableDraft(fields=fields, items=items)


# --- Run assembly ---------------------------------------------------------------


@dataclass(frozen=True)
class EmptyAlertRun:
    """The explicit outcome when zero signals exist — never padded."""

    as_of: datetime
    reason_en: str
    reason_zh: str


@dataclass(frozen=True)
class AlertRun:
    """The built-but-unverified run: selections and one draft per selection."""

    as_of: datetime
    signal_count: int
    selections: tuple[AlertSelection, ...]
    drafts: tuple[AlertDraft, ...]
    empty_report: EmptyAlertRun | None  # set when there are no signals at all


def _scenario(items: Sequence[SignalRow], score: float) -> tuple[str, str]:
    """Qualitative scenario (design doc §6): direction words, never ranges."""
    if not items:
        return (
            f"Neutral — no new signals this week; the Alpha Score {score:.1f} "
            "reflects earlier signals until they age out.",
            f"中性——本周无新信号；Alpha Score {score:.1f} 反映的是此前信号，直至其过时。",
        )
    positives = sum(1 for r in items if r.direction == "positive")
    negatives = sum(1 for r in items if r.direction == "negative")
    if positives > negatives:
        return (
            "Constructive — the cited signals added positive momentum this week; "
            "the view stands while they do, and weakens if they are superseded.",
            "偏正面——所引信号本周带来正面动量；信号成立期间观点有效，被取代则减弱。",
        )
    if negatives > positives:
        return (
            "Cautious — the cited signals weigh negative this week; "
            "the view stands while they do, and reverses if they are superseded.",
            "偏谨慎——所引信号本周偏负面；信号成立期间观点有效，被取代则反转。",
        )
    return (
        "Balanced — the cited signals point both ways; the score captures the net.",
        "中性——所引信号方向不一，评分反映其净值。",
    )


def build_alerts(
    rows: Sequence[Mapping[str, Any]],
    universe: Sequence[CompanyProfile],
    as_of: datetime,
    *,
    watchlist: Sequence[WatchlistEntry] = (),
) -> AlertRun:
    """Assemble the weekly alert run — selection + drafting, no verification yet.

    Every selection gets a draft; the Verification Agent decides which ones
    survive. Zero signal rows is an explicit empty run (an honest no-signal
    week is reported, never padded).
    """
    if as_of.tzinfo is None:
        raise AlertInputError("as_of must be timezone-aware")
    if not rows:
        return AlertRun(
            as_of=as_of,
            signal_count=0,
            selections=(),
            drafts=(),
            empty_report=EmptyAlertRun(
                as_of=as_of,
                reason_en=(
                    "No signals in the store for this run — nothing to brief. "
                    "An explicit empty run beats a padded alert."
                ),
                reason_zh="本周期存储中无信号——无可简报内容。明确的空报告胜过凑数的警报。",
            ),
        )

    selections = select_top10(rows, universe, as_of, watchlist=watchlist)
    drafts = []
    for selection in selections:
        items = driving_signals(rows, selection.ticker, as_of)
        scenario_en, scenario_zh = _scenario(items, selection.score)
        drafts.append(
            AlertDraft(
                as_of=as_of,
                selection=selection,
                items=tuple(items),
                scenario_en=scenario_en,
                scenario_zh=scenario_zh,
                invalidation_en=_INVALIDATION_EN,
                invalidation_zh=_INVALIDATION_ZH,
            )
        )
    return AlertRun(
        as_of=as_of,
        signal_count=len(rows),
        selections=tuple(selections),
        drafts=tuple(drafts),
        empty_report=None,
    )


# --- Verification, history rows, staging ----------------------------------------


@dataclass(frozen=True)
class StagedAlert:
    """One alert's verification verdict and rendered surfaces."""

    draft: AlertDraft
    status: str  # pending_approval | rejected (dropped from the run)
    issues: tuple[VerificationIssue, ...]
    brief_en: str
    brief_zh: str

    @property
    def passed(self) -> bool:
        return self.status == STATUS_PENDING_APPROVAL


@dataclass(frozen=True)
class AlertHistoryRow:
    """One ``alert_history`` row — signal-linked, unapproved, unsent."""

    alert_id: str
    alert_date: str
    ticker: str
    alpha_score: float
    score_delta: float
    brief_en: str
    brief_zh: str
    signal_ids: list[str]
    approved: bool
    sent_at: None


@dataclass(frozen=True)
class StagedAlertRun:
    """The workflow-facing artifact: statuses, title, markdown, history rows."""

    status: str
    title: str
    markdown: str
    alerts: tuple[StagedAlert, ...]
    history_rows: tuple[AlertHistoryRow, ...]


def _brief_lines(draft: AlertDraft) -> tuple[str, str]:
    selection = draft.selection
    n = len(draft.items)
    brief_en = (
        f"Alpha Score {selection.score:.1f} ({selection.delta:+.1f} vs prior week). "
        f"{draft.scenario_en} Signals cited: {n}."
    )
    brief_zh = (
        f"Alpha Score {selection.score:.1f}（较上周 {selection.delta:+.1f}）。"
        f"{draft.scenario_zh} 引用信号：{n} 条。"
    )
    return brief_en, brief_zh


def stage_alerts(run: AlertRun, *, source_checker: SourceChecker) -> StagedAlertRun:
    """Verify every drafted alert, render the staged issue, build history rows.

    Dropped alerts keep their machine-readable reasons; passing alerts get
    ``alert_history`` rows (approved=false, sent_at=null — recording is not
    sending). The run status follows the writer's three-way contract.
    """
    if run.empty_report is not None:
        verification = verify_empty_run(run.empty_report.reason_en, run.empty_report.reason_zh)
        return StagedAlertRun(
            status=STATUS_EMPTY_RUN,
            title=alert_issue_title(run.as_of, STATUS_EMPTY_RUN),
            markdown=render_empty_run(run.empty_report, verification),
            alerts=(),
            history_rows=(),
        )

    staged: list[StagedAlert] = []
    for draft in run.drafts:
        verification = verify_draft(draft.verification_input(), source_checker=source_checker)
        status = STATUS_PENDING_APPROVAL if verification.passed else STATUS_REJECTED
        brief_en, brief_zh = _brief_lines(draft)
        staged.append(
            StagedAlert(
                draft=draft,
                status=status,
                issues=verification.issues,
                brief_en=brief_en,
                brief_zh=brief_zh,
            )
        )

    passed = [a for a in staged if a.passed]
    status = STATUS_PENDING_APPROVAL if passed else STATUS_REJECTED
    history = tuple(
        AlertHistoryRow(
            alert_id=a.draft.alert_id,
            alert_date=run.as_of.strftime("%Y-%m-%d"),
            ticker=a.draft.selection.ticker,
            alpha_score=round(a.draft.selection.score, 1),
            score_delta=round(a.draft.selection.delta, 1),
            brief_en=a.brief_en,
            brief_zh=a.brief_zh,
            signal_ids=[row.signal_id for row in a.draft.items],
            approved=False,
            sent_at=None,
        )
        for a in passed
    )
    return StagedAlertRun(
        status=status,
        title=alert_issue_title(run.as_of, status),
        markdown=render_alert_run(run, staged, status),
        alerts=tuple(staged),
        history_rows=history,
    )


def alert_issue_title(as_of: datetime, status: str) -> str:
    """The staged GitHub issue title — the status is the first thing read."""
    date_str = as_of.strftime("%Y-%m-%d")
    if status == STATUS_PENDING_APPROVAL:
        return f"[weekly-alert] {date_str} — PENDING HUMAN APPROVAL"
    if status == STATUS_REJECTED:
        return f"[weekly-alert] {date_str} — REJECTED BY VERIFICATION (never published)"
    if status == STATUS_EMPTY_RUN:
        return f"[weekly-alert] {date_str} — EMPTY RUN (no signals to brief)"
    raise AlertInputError(f"unknown staging status: {status!r}")


# --- Rendering ------------------------------------------------------------------


def _sources_md(row: SignalRow) -> str:
    links = [
        f"[{s.get('type') or 'src'}]({s.get('url', '')})" for s in row.sources if s.get("url")
    ]
    return " · ".join(links) if links else "—"


def _why_it_matters(draft: AlertDraft) -> str:
    entry = draft.selection.entry
    return (
        f"{entry.company} is covered under **{entry.category}** (themes: {entry.themes}; "
        f"exposure: {entry.exposure_type}). Covered thesis: {entry.thesis}"
    )


def _why_it_matters_zh(draft: AlertDraft) -> str:
    entry = draft.selection.entry
    return (
        f"{entry.company} 属 **{entry.category}** 类别（主题：{entry.themes}；"
        f"敞口类型：{entry.exposure_type}）。覆盖论点（英文原文）：{entry.thesis}"
    )


def _selection_note(selection: AlertSelection) -> str:
    if selection.source == "watchlist":
        return "selection: owner watchlist (pinned)"
    return f"selection: top-{ALERT_COUNT} ranking, rank {selection.rank}"


def render_alert_run(run: AlertRun, staged: Sequence[StagedAlert], status: str) -> str:
    """The staged issue body: gate banner first, bilingual alerts second."""
    as_of_iso = run.as_of.isoformat().replace("+00:00", "Z")
    passed = [a for a in staged if a.passed]
    dropped = [a for a in staged if not a.passed]
    lines: list[str] = [
        _APPROVAL_BANNER,
        "",
        f"**Status:** {status.replace('_', ' ').upper()} · **As of:** {as_of_iso}",
        f"**Alerts drafted:** {len(staged)} · passed: {len(passed)} · "
        f"dropped by verification: {len(dropped)}",
    ]
    strength_four = [
        row.signal_id for a in passed for row in a.draft.items if row.strength >= 4
    ]
    if strength_four:
        lines += [
            "",
            "> ⚠️ Contains strength ≥ 4 signal(s) — explicit owner confirmation required: "
            + ", ".join(f"`{s}`" for s in strength_four),
        ]
    if dropped:
        lines += _render_drops(dropped)
    lines += _render_section_en(passed)
    lines += _render_section_zh(passed)
    lines += [
        "",
        "---",
        "Assembled deterministically by `pipelines.agents.alerts` from the weekly scan's "
        "signal rows · gated by `pipelines.agents.verifier` · recorded in `alert_history` "
        "only after the research owner approves. Research content, not investment advice.",
    ]
    return "\n".join(lines) + "\n"


def _render_drops(dropped: Sequence[StagedAlert]) -> list[str]:
    lines = [
        "",
        "## Dropped by verification / 核验未通过（已丢弃，永不发送）",
        "",
        "| ticker | code | field | detail |",
        "| --- | --- | --- | --- |",
    ]
    for alert in dropped:
        for issue in alert.issues:
            lines.append(
                f"| `{alert.draft.selection.ticker}` | `{issue.code}` | `{issue.field}` "
                f"| {issue.detail} |"
            )
    return lines


def _render_section_en(passed: Sequence[StagedAlert]) -> list[str]:
    lines = ["", "## Weekly alerts (EN)", ""]
    for i, alert in enumerate(passed, 1):
        draft = alert.draft
        selection = draft.selection
        lines += [
            f"### {i}. {draft.title_en}",
            "",
            f"- `{selection.ticker}` · {selection.company} · {_selection_note(selection)}",
            f"- Why it matters: {_why_it_matters(draft)}",
            f"  (universe source: {selection.entry.source_url})",
            f"- Scenario (qualitative): {draft.scenario_en}",
            f"- Invalidation: {draft.invalidation_en}",
            f"- Signals driving this alert ({len(draft.items)}):",
        ]
        for row in draft.items:
            lines += [
                f"  - `{row.signal_id}` · {row.category} · direction {row.direction} · "
                f"strength {row.strength}/5 — {row.summary_en}",
                f"    - Sources: {_sources_md(row)}",
                f"    - Invalidation: {row.invalidation}",
            ]
    return lines


def _render_section_zh(passed: Sequence[StagedAlert]) -> list[str]:
    lines = ["", "## 每周警报（中文）", ""]
    for i, alert in enumerate(passed, 1):
        draft = alert.draft
        selection = draft.selection
        lines += [
            f"### {i}. {draft.title_zh}",
            "",
            f"- `{selection.ticker}` · {selection.company} · {_selection_note(selection)}",
            f"- 为什么重要：{_why_it_matters_zh(draft)}",
            f"  （宇宙数据来源：{selection.entry.source_url}）",
            f"- 情景（定性）：{draft.scenario_zh}",
            f"- 失效条件：{draft.invalidation_zh}",
            f"- 驱动本警报的信号（{len(draft.items)} 条）：",
        ]
        for row in draft.items:
            lines += [
                f"  - `{row.signal_id}` · {row.category} · 方向 {row.direction} · "
                f"强度 {row.strength}/5 — {row.summary_zh}",
                f"    - 来源：{_sources_md(row)}",
                f"    - 失效条件：{row.invalidation}",
            ]
    return lines


def render_empty_run(report: EmptyAlertRun, verification: Any) -> str:
    """The staged issue body for an explicit empty run."""
    as_of_iso = report.as_of.isoformat().replace("+00:00", "Z")
    return "\n".join(
        [
            _APPROVAL_BANNER,
            "",
            f"**Status:** EMPTY RUN · **Verification:** "
            f"{'PASSED' if verification.passed else 'FAILED'} · **As of:** {as_of_iso}",
            "",
            "## Empty run / 空报告",
            "",
            report.reason_en,
            "",
            report.reason_zh,
            "",
            "---",
            "Assembled deterministically by `pipelines.agents.alerts` · an explicit empty run "
            "is the honest outcome when the store has no signals — nothing is padded or sent.",
        ]
    ) + "\n"


# --- alert_history persistence ---------------------------------------------------

_INSERT_ALERT_SQL = """\
INSERT INTO alert_history (alert_id, alert_date, ticker, alpha_score, score_delta,
                           brief_en, brief_zh, signal_ids, approved, sent_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (alert_id) DO NOTHING"""


class HistoryConnection(Protocol):
    """Minimal transactional connection (same protocol the scan publisher uses)."""

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> Any: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


def publish_alert_history(conn: HistoryConnection, rows: Sequence[AlertHistoryRow]) -> list[str]:
    """Insert ``alert_history`` rows; idempotent on ``alert_id``.

    ``ON CONFLICT DO NOTHING`` — a re-run never overwrites a row the owner
    has approved. Any failure rolls back the whole batch: history either
    records the run or does not, never half-way.
    """
    if not rows:
        return []
    try:
        for row in rows:
            conn.execute(
                _INSERT_ALERT_SQL,
                (
                    row.alert_id,
                    row.alert_date,
                    row.ticker,
                    row.alpha_score,
                    row.score_delta,
                    row.brief_en,
                    row.brief_zh,
                    row.signal_ids,
                    row.approved,
                    row.sent_at,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return [row.alert_id for row in rows]


# --- CLI --------------------------------------------------------------------------


class _NoNetworkChecker:
    """Offline/development mode: structural source checks only, no probes."""

    def reachable(self, url: str) -> bool:
        return True


def _resolve_as_of(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(UTC)
    as_of = datetime.fromisoformat(raw)
    if as_of.tzinfo is None:
        raise AlertInputError("--as-of must be a timezone-aware ISO timestamp")
    return as_of


def _load_rows(args: argparse.Namespace, as_of: datetime) -> list[dict[str, Any]]:
    if args.signals_file:
        loaded = json.loads(Path(args.signals_file).read_text(encoding="utf-8"))
        if not isinstance(loaded, list) or not all(isinstance(r, Mapping) for r in loaded):
            raise AlertInputError("--signals-file must contain a JSON array of signal rows")
        return list(loaded)

    base_url = os.environ.get("SUPABASE_URL", "")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not base_url or not service_key:
        raise AlertInputError(
            "no --signals-file given and SUPABASE_URL / SUPABASE_SERVICE_KEY are not set — "
            "refusing to guess (a silent empty run would mask misconfiguration)"
        )
    since = (as_of - timedelta(days=HISTORY_DAYS)).isoformat()
    return fetch_signals_supabase(base_url, service_key, since)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m pipelines.agents.alerts",
        description="Select, draft, verify, and stage the weekly top-10 alerts (never sends).",
    )
    parser.add_argument(
        "--signals-file",
        help="JSON file of canonical signal rows (fixture mode); default: Supabase REST",
    )
    parser.add_argument(
        "--watchlist-csv",
        default=None,
        help="curated override list (default: the committed pipelines/data/watchlist_top10.csv)",
    )
    parser.add_argument(
        "--source-checker",
        choices=("http", "none"),
        default="http",
        help="http = probe source URLs (runtime); none = structural checks only (offline/dev)",
    )
    parser.add_argument("--out-dir", default="weekly-alert-out", help="output directory")
    parser.add_argument(
        "--as-of", help="override the clock (tz-aware ISO 8601); default: now UTC"
    )
    parser.add_argument(
        "--publish-history",
        action="store_true",
        help="record passing alerts in alert_history (requires SUPABASE_DB_URL)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        as_of = _resolve_as_of(args.as_of)
        rows = _load_rows(args, as_of)

        # The `data` package (pipelines/data/) ships beside the package, not in
        # the wheel — same sys.path treatment the weekly report CLI applies.
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from data.universe import WATCHLIST_CSV, load_universe

        universe = load_universe()
        watchlist = load_top10_watchlist(args.watchlist_csv or WATCHLIST_CSV)
        run = build_alerts(rows, universe, as_of, watchlist=watchlist)
    except (AlertInputError, WriterInputError, OSError, json.JSONDecodeError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    checker: SourceChecker = (
        HttpSourceChecker() if args.source_checker == "http" else _NoNetworkChecker()
    )
    staged_run = stage_alerts(run, source_checker=checker)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    history_payload = [asdict(row) for row in staged_run.history_rows]
    payload = {
        "status": staged_run.status,
        "issue_title": staged_run.title,
        "as_of": as_of.isoformat(),
        "signal_count": run.signal_count,
        "alert_count": len(staged_run.alerts),
        "passed": sum(1 for a in staged_run.alerts if a.passed),
        "dropped": [
            {"ticker": a.draft.selection.ticker, "reasons": [asdict(i) for i in a.issues]}
            for a in staged_run.alerts
            if not a.passed
        ],
        "history_recorded": [],
    }

    if args.publish_history:
        db_url = os.environ.get("SUPABASE_DB_URL", "")
        if not db_url:
            print(
                "error: --publish-history requires SUPABASE_DB_URL in the environment",
                file=sys.stderr,
            )
            return 2
        try:
            import psycopg

            with psycopg.connect(db_url) as conn:
                payload["history_recorded"] = publish_alert_history(
                    conn, staged_run.history_rows
                )
        except Exception as err:  # the run goes red with the raw reason
            print(f"error: alert_history publish failed: {err}", file=sys.stderr)
            return 2

    (out_dir / "issue.md").write_text(staged_run.markdown, encoding="utf-8")
    (out_dir / "status.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out_dir / "alerts.json").write_text(
        json.dumps(
            {
                "alerts": [
                    {
                        "alert_id": a.draft.alert_id,
                        "ticker": a.draft.selection.ticker,
                        "company": a.draft.selection.company,
                        "status": a.status,
                        "score": a.draft.selection.score,
                        "delta": a.draft.selection.delta,
                        "selection": a.draft.selection.source,
                        "brief_en": a.brief_en,
                        "brief_zh": a.brief_zh,
                        "signal_ids": [r.signal_id for r in a.draft.items],
                        "issues": [asdict(i) for i in a.issues],
                    }
                    for a in staged_run.alerts
                ],
                "history_rows": history_payload,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (out_dir / "history_rows.json").write_text(
        json.dumps(history_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(f"weekly-alerts: {staged_run.status} — {staged_run.title} (staged in {out_dir})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
