"""Writer Agent: the bilingual Daily Alpha brief (design doc §7.2, §8.1 stage 4).

Deterministic by contract (build spec: "deterministic-first design — LLM
only classifies and drafts"): the brief is assembled from canonical signal
rows, and company annotations come from the Alpha Score engine on ``main``.
Nothing is invented here — summaries are the store's own bilingual
sentences, and every claim keeps its source links and invalidation
condition.

Display rules (design doc §6) bind the staged issue too: it lives on a
public repository, so it is free-tier surface. Direction and qualitative
descriptions are rendered; ``scenario_range`` and ``probability_edge``
numbers are never rendered.

Send wiring lives in operations (the weekly-alerts task): :func:`stage_brief`
produces the staged issue surface — nothing here emails or publishes.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from pipelines.agents.verifier import (
    MAX_BRIEF_ITEMS,
    SourceChecker,
    SourceRef,
    VerifiableDraft,
    VerifiableItem,
    VerificationResult,
    verify_draft,
    verify_empty_run,
)
from pipelines.scoring import ScoreDelta, SignalEvent, score_deltas

#: §7.2: the brief carries 3–5 signals; selection is strength-desc then
#: recency-desc, capped at MAX_ITEMS (the verifier rejects over-long briefs).
MAX_ITEMS = MAX_BRIEF_ITEMS

#: Strength floor for brief-worthiness: capacity-change context events
#: (strength 1) are de-weighted by design (build spec, locked decision 3).
MIN_STRENGTH = 2

#: Default item-selection window before ``as_of`` — one trading-day cycle.
ITEM_WINDOW_HOURS = 24

#: Alpha Score annotation window for the brief ("Δ7d").
SCORE_DELTA_WINDOW_DAYS = 7

#: Supabase lookback for the Alpha Score history window.
HISTORY_DAYS = 90

#: Label for rows with no theme (the column is nullable).
UNCATEGORIZED = "Uncategorized"

# Staging statuses — machine-readable, read by the workflow from status.json.
STATUS_PENDING_APPROVAL = "pending_approval"
STATUS_REJECTED = "rejected"
STATUS_EMPTY_RUN = "empty_run"

_DIRECTION_ZH = {"positive": "正面", "negative": "负面", "neutral": "中性"}
_SENS_ZH = {"high": "高", "medium": "中", "low": "低", "mixed": "混合", "unknown": "未标注"}

_REQUIRED_ROW_FIELDS = ("signal_id", "summary_en", "summary_zh", "invalidation")

_APPROVAL_BANNER = (
    "> ⛔ **Human approval gate (design doc §8.1):** this brief is staged for review only. "
    "Nothing is emailed or published automatically — a research owner must approve on this "
    "issue before any send. Approval and delivery wiring lives in operations, not in this workflow."
)


class WriterInputError(ValueError):
    """A signal row (or brief input) does not meet the canonical store contract."""


@dataclass(frozen=True)
class SignalRow:
    """A normalized canonical signal row (db/migrations/0001_signal_store.sql).

    Deliberately excludes ``scenario_range``/``probability_edge``: the brief
    is free-tier surface and renders direction and qualitative context only
    (design doc §6 display rules).
    """

    signal_id: str
    category: str
    event_type: str
    tickers: tuple[str, ...]
    theme: str | None
    summary_en: str
    summary_zh: str
    direction: str
    strength: int
    horizon_days: int | None
    macro_overlay: dict[str, Any]
    invalidation: str
    sources: tuple[dict[str, Any], ...]
    published_at: datetime

    def to_event(self) -> SignalEvent:
        """The row as the Alpha Score engine sees it (validated there)."""
        return SignalEvent(
            signal_id=self.signal_id,
            category=self.category,
            direction=self.direction,
            strength=self.strength,
            published_at=self.published_at,
            tickers=self.tickers,
            event_type=self.event_type,
        )


@dataclass(frozen=True)
class DailyAlphaDraft:
    """The §7.2 brief in both languages, plus its Alpha Score annotations."""

    as_of: datetime
    title_en: str
    title_zh: str
    takeaway_en: str
    takeaway_zh: str
    items: tuple[SignalRow, ...]
    theme_heat: tuple[tuple[str, int], ...]
    macro_en: str
    macro_zh: str
    scores: Mapping[str, ScoreDelta]

    def verification_input(self) -> VerifiableDraft:
        """The draft's full text surface, shaped for the Verification Agent."""
        fields = {
            "title_en": self.title_en,
            "title_zh": self.title_zh,
            "takeaway_en": self.takeaway_en,
            "takeaway_zh": self.takeaway_zh,
            "macro_en": self.macro_en,
            "macro_zh": self.macro_zh,
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


@dataclass(frozen=True)
class EmptyRunReport:
    """The explicit outcome when zero signals qualify — never padded."""

    as_of: datetime
    total_count: int
    qualifying_count: int
    item_window_hours: int
    min_strength: int
    reason_en: str
    reason_zh: str


@dataclass(frozen=True)
class StagedBrief:
    """The workflow-facing artifact: status, issue title, markdown, verdict."""

    status: str
    title: str
    markdown: str
    verification: VerificationResult


def normalize_row(row: Mapping[str, Any]) -> SignalRow:
    """Validate and type one canonical signal row; loud on contract breaks."""
    signal_id = str(row.get("signal_id", "")).strip()
    missing = [f for f in _REQUIRED_ROW_FIELDS if not str(row.get(f, "")).strip()]
    if missing:
        raise WriterInputError(
            f"signal row {signal_id or '<unnamed>'!r} missing required fields: {missing}"
        )

    published = row.get("published_at")
    if isinstance(published, str):
        published = datetime.fromisoformat(published)
    if not isinstance(published, datetime) or published.tzinfo is None:
        raise WriterInputError(
            f"signal {signal_id!r}: published_at must be a timezone-aware timestamp (timestamptz)"
        )

    strength = int(row.get("strength", 1))
    if not 1 <= strength <= 5:
        raise WriterInputError(f"signal {signal_id!r}: strength must be 1-5, got {strength}")

    direction = str(row.get("direction", "neutral"))
    if direction not in _DIRECTION_ZH:
        raise WriterInputError(f"signal {signal_id!r}: invalid direction {direction!r}")

    sources_raw: Any = row.get("sources") or []
    if isinstance(sources_raw, (str, bytes)) or not isinstance(sources_raw, Sequence):
        raise WriterInputError(f"signal {signal_id!r}: sources must be a JSON array of objects")
    tickers_raw: Any = row.get("tickers") or []
    if isinstance(tickers_raw, (str, bytes)) or not isinstance(tickers_raw, Sequence):
        raise WriterInputError(f"signal {signal_id!r}: tickers must be a JSON array of strings")

    return SignalRow(
        signal_id=signal_id,
        category=str(row.get("category", "other")),
        event_type=str(row.get("event_type", "other")),
        tickers=tuple(str(t) for t in tickers_raw),
        theme=(str(row["theme"]) if row.get("theme") else None),
        summary_en=str(row["summary_en"]),
        summary_zh=str(row["summary_zh"]),
        direction=direction,
        strength=strength,
        horizon_days=int(row["horizon_days"]) if row.get("horizon_days") is not None else None,
        macro_overlay=dict(row.get("macro_overlay") or {}),
        invalidation=str(row["invalidation"]),
        sources=tuple(dict(s) for s in sources_raw),
        published_at=published,
    )


def build_daily_alpha(
    rows: Sequence[Mapping[str, Any]],
    as_of: datetime,
    *,
    item_window_hours: int = ITEM_WINDOW_HOURS,
    max_items: int = MAX_ITEMS,
    min_strength: int = MIN_STRENGTH,
) -> DailyAlphaDraft | EmptyRunReport:
    """Assemble the §7.2 brief from canonical signal rows — or report empty.

    Item selection: published within ``item_window_hours`` of ``as_of``,
    strength ≥ ``min_strength``, ranked strength-desc then recency-desc,
    capped at five. Alpha Scores are computed from the full row set (the
    90-day history the caller passes in), not just the item window.
    """
    if as_of.tzinfo is None:
        raise WriterInputError("as_of must be timezone-aware")
    if item_window_hours <= 0:
        raise WriterInputError(f"item_window_hours must be positive, got {item_window_hours}")

    history = [normalize_row(row) for row in rows]
    window_start = as_of - timedelta(hours=item_window_hours)
    candidates = [
        r
        for r in history
        if window_start <= r.published_at <= as_of and r.strength >= min_strength
    ]
    if not candidates:
        return _empty_run(as_of, len(history), item_window_hours, min_strength)

    candidates.sort(key=lambda r: (-r.strength, -r.published_at.timestamp(), r.signal_id))
    chosen = tuple(candidates[: min(max_items, MAX_ITEMS)])
    theme_heat = tuple(
        sorted(
            Counter(r.theme or UNCATEGORIZED for r in candidates).items(),
            key=lambda kv: (-kv[1], kv[0]),
        )
    )
    date_str = as_of.strftime("%Y-%m-%d")
    tone_en, tone_zh = _tone(chosen)
    lead_theme, lead_count = theme_heat[0]

    return DailyAlphaDraft(
        as_of=as_of,
        title_en=f"Daily Alpha · {date_str}",
        title_zh=f"每日信号 · {date_str}",
        takeaway_en=(
            f"{len(chosen)} signals cleared the bar today, net {tone_en}, "
            f"led by {lead_theme} ({lead_count})."
        ),
        takeaway_zh=(
            f"今日 {len(chosen)} 条信号过线，整体{tone_zh}，"
            f"以「{lead_theme}」为主（{lead_count} 条）。"
        ),
        items=chosen,
        theme_heat=theme_heat,
        macro_en=_macro_en(chosen),
        macro_zh=_macro_zh(chosen),
        scores=_score_annotations(history, chosen, as_of),
    )


def stage_brief(
    built: DailyAlphaDraft | EmptyRunReport, *, source_checker: SourceChecker
) -> StagedBrief:
    """Run the verification gate and render the staged issue surface.

    This is the whole writer-side workflow: build → verify → render. It
    stages; it never sends or publishes.
    """
    if isinstance(built, EmptyRunReport):
        verification = verify_empty_run(built.reason_en, built.reason_zh)
        status = STATUS_EMPTY_RUN
        markdown = render_empty_run(built, verification)
    else:
        verification = verify_draft(built.verification_input(), source_checker=source_checker)
        status = STATUS_PENDING_APPROVAL if verification.passed else STATUS_REJECTED
        markdown = render_brief(built, verification)
    return StagedBrief(
        status=status,
        title=issue_title(built.as_of, status),
        markdown=markdown,
        verification=verification,
    )


def issue_title(as_of: datetime, status: str) -> str:
    """The staged GitHub issue title — the status is the first thing read."""
    date_str = as_of.strftime("%Y-%m-%d")
    if status == STATUS_PENDING_APPROVAL:
        return f"[daily-alpha] {date_str} — PENDING HUMAN APPROVAL"
    if status == STATUS_REJECTED:
        return f"[daily-alpha] {date_str} — REJECTED BY VERIFICATION (never published)"
    if status == STATUS_EMPTY_RUN:
        return f"[daily-alpha] {date_str} — EMPTY RUN (no qualifying signals)"
    raise WriterInputError(f"unknown staging status: {status!r}")


def render_brief(draft: DailyAlphaDraft, verification: VerificationResult) -> str:
    """The staged issue body for a draft: gate banner first, brief second."""
    status = (
        "PENDING HUMAN APPROVAL — not sent, not published"
        if verification.passed
        else "REJECTED BY VERIFICATION — must not be published or sent"
    )
    as_of_iso = draft.as_of.isoformat().replace("+00:00", "Z")
    lines: list[str] = [
        _APPROVAL_BANNER,
        "",
        f"**Status:** {status}  ",
        f"**Verification:** {'PASSED' if verification.passed else 'FAILED'} "
        f"({len(verification.issues)} issue(s))  ",
        f"**As of:** {as_of_iso}",
    ]
    strength_four = [r.signal_id for r in draft.items if r.strength >= 4]
    if strength_four:
        lines += [
            "",
            "> ⚠️ Contains strength ≥ 4 signal(s) — explicit owner confirmation required: "
            + ", ".join(f"`{s}`" for s in strength_four),
        ]
    if not verification.passed:
        lines += _render_rejections(verification)
    lines += _render_section_en(draft)
    lines += _render_section_zh(draft)
    lines += [
        "",
        "---",
        "Assembled deterministically by `pipelines.agents.writer` from canonical signal rows · "
        "gated by `pipelines.agents.verifier` · research content, not investment advice.",
    ]
    return "\n".join(lines) + "\n"


def render_empty_run(report: EmptyRunReport, verification: VerificationResult) -> str:
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
            f"**Candidate rows seen:** {report.total_count} · qualifying: "
            f"{report.qualifying_count} · item window: {report.item_window_hours}h · "
            f"minimum strength: {report.min_strength}",
            "",
            "---",
            "Assembled deterministically by `pipelines.agents.writer` · an explicit empty run "
            "is the honest outcome when no signal clears the bar — nothing is padded or sent.",
        ]
    ) + "\n"


def _empty_run(
    as_of: datetime, total: int, item_window_hours: int, min_strength: int
) -> EmptyRunReport:
    return EmptyRunReport(
        as_of=as_of,
        total_count=total,
        qualifying_count=0,
        item_window_hours=item_window_hours,
        min_strength=min_strength,
        reason_en=(
            f"No signals met the Daily Alpha bar in the last {item_window_hours} hours "
            f"(0 of {total} candidate rows; minimum strength {min_strength}). "
            "Nothing is published — an explicit empty run beats a padded brief."
        ),
        reason_zh=(
            f"过去 {item_window_hours} 小时内没有达到 Daily Alpha 门槛的信号"
            f"（候选 {total} 条，最低强度 {min_strength}）。今日空发：明确的空报告胜过凑数。"
        ),
    )


def _tone(chosen: Sequence[SignalRow]) -> tuple[str, str]:
    positives = sum(1 for r in chosen if r.direction == "positive")
    negatives = sum(1 for r in chosen if r.direction == "negative")
    if positives > negatives:
        return "constructive", "偏正面"
    if negatives > positives:
        return "cautious", "偏负面"
    return "balanced", "中性"


def _macro_lines(rows: Sequence[SignalRow]) -> tuple[str, str]:
    rate_values = [
        str(r.macro_overlay["rate_sensitivity"])
        for r in rows
        if r.macro_overlay.get("rate_sensitivity")
    ]
    usd_values = [
        str(r.macro_overlay["usd_sensitivity"])
        for r in rows
        if r.macro_overlay.get("usd_sensitivity")
    ]
    if not rate_values and not usd_values:
        return (
            "No macro overlay data on today's signals.",
            "今日信号暂无宏观叠加数据。",
        )
    rate = _dominant(rate_values)
    usd = _dominant(usd_values)
    return (
        f"Rate sensitivity: {rate} · USD sensitivity: {usd}.",
        f"利率敏感度：{_SENS_ZH.get(rate, rate)} · 美元敏感度：{_SENS_ZH.get(usd, usd)}。",
    )


def _macro_en(rows: Sequence[SignalRow]) -> str:
    return _macro_lines(rows)[0]


def _macro_zh(rows: Sequence[SignalRow]) -> str:
    return _macro_lines(rows)[1]


def _dominant(values: Sequence[str]) -> str:
    if not values:
        return "unknown"
    counts = Counter(values)
    if len(counts) == 1:
        return next(iter(counts))
    return "mixed"


def _score_annotations(
    history: Iterable[SignalRow], chosen: Sequence[SignalRow], as_of: datetime
) -> dict[str, ScoreDelta]:
    tickers = sorted({t for r in chosen for t in r.tickers})
    if not tickers:
        return {}
    events = [r.to_event() for r in history if r.published_at <= as_of]
    return {
        d.ticker: d
        for d in score_deltas(
            events, tickers, as_of, window_days=SCORE_DELTA_WINDOW_DAYS
        )
    }


def _render_rejections(verification: VerificationResult) -> list[str]:
    lines = [
        "",
        "## Verification rejections / 核验未通过（机器可读）",
        "",
        "| code | field | detail |",
        "| --- | --- | --- |",
    ]
    lines += [f"| `{i.code}` | `{i.field}` | {i.detail} |" for i in verification.issues]
    return lines


def _score_note(ticker: str, scores: Mapping[str, ScoreDelta]) -> str:
    delta = scores.get(ticker)
    if delta is None:
        return ticker
    return (
        f"{ticker} (Alpha Score {delta.score:.1f}, "
        f"Δ{SCORE_DELTA_WINDOW_DAYS}d {delta.delta:+.1f})"
    )


def _tickers_md(row: SignalRow, scores: Mapping[str, ScoreDelta]) -> str:
    if not row.tickers:
        return "—"
    return ", ".join(_score_note(t, scores) for t in row.tickers)


def _sources_md(row: SignalRow) -> str:
    links = [
        f"[{s.get('type') or 'src'}]({s.get('url', '')})" for s in row.sources if s.get("url")
    ]
    return " · ".join(links) if links else "—"


def _render_section_en(draft: DailyAlphaDraft) -> list[str]:
    lines = [
        "",
        f"## {draft.title_en}",
        "",
        f"**Today in one sentence:** {draft.takeaway_en}",
        "",
        f"**Top signals ({len(draft.items)}):**",
    ]
    for i, row in enumerate(draft.items, 1):
        lines += [
            "",
            f"### {i}. {row.summary_en}",
            f"- `{row.signal_id}` · {row.category} · direction {row.direction} · "
            f"strength {row.strength}/5",
            f"- Tickers: {_tickers_md(row, draft.scores)}",
            f"- Theme: {row.theme or UNCATEGORIZED}",
            f"- Sources: {_sources_md(row)}",
            f"- Invalidation: {row.invalidation}",
        ]
    heat = ", ".join(f"{theme} ×{count}" for theme, count in draft.theme_heat)
    lines += ["", f"**Theme heat:** {heat or '—'}", "", f"**Macro, one line:** {draft.macro_en}"]
    return lines


def _render_section_zh(draft: DailyAlphaDraft) -> list[str]:
    lines = [
        "",
        f"## {draft.title_zh}",
        "",
        f"**今日要点：** {draft.takeaway_zh}",
        "",
        f"**重点信号（{len(draft.items)} 条）：**",
    ]
    for i, row in enumerate(draft.items, 1):
        direction = _DIRECTION_ZH.get(row.direction, row.direction)
        lines += [
            "",
            f"### {i}. {row.summary_zh}",
            f"- `{row.signal_id}` · {row.category} · 方向{direction} · 强度 {row.strength}/5",
            f"- 相关标的：{_tickers_md(row, draft.scores)}",
            f"- 主题：{row.theme or UNCATEGORIZED}",
            f"- 来源：{_sources_md(row)}",
            f"- 失效条件：{row.invalidation}",
        ]
    heat = "，".join(f"{theme} ×{count}" for theme, count in draft.theme_heat)
    lines += ["", f"**主题热度：** {heat or '—'}", "", f"**宏观一句话：** {draft.macro_zh}"]
    return lines
