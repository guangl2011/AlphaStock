"""The V2 transition grammar: per-project event classification.

The unit of signal is the status transition, not raw queue megawatts (build
spec, locked decision 3). This module owns the grammar for ONE project
observed in two scans: forward progress, backward moves (allowed, and
themselves signal), withdrawal, re-entry after withdrawal, and capacity
changes as de-weighted context. Snapshot-level orchestration lives in
``queue_diff.py``; this module is pure pair logic so it can be tested
exhaustively without fixtures.

Strength and direction are deterministic and explainable — the Alpha Score
stage re-weights downstream (``scoring/alpha_score.py``), so this layer never
invents probabilities. Summaries are bilingual by contract (design doc §4.2);
capacity appears in text only as context, never as a demand claim.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from pipelines.ingest.queues.base import QueueProject
from pipelines.ingest.queues.ladder import RUNG_ZH, WITHDRAWN, ladder_rank

#: Canonical event types, matching signals.event_type CHECK constraint.
EVENT_NEW_PROJECT = "new_project"
EVENT_STATUS_TRANSITION = "status_transition"
EVENT_CAPACITY_CHANGE = "capacity_change"
EVENT_WITHDRAWAL = "withdrawal"

#: Relative capacity movement that counts as a context event (not a demand claim).
CAPACITY_CHANGE_PCT = 0.10

#: Deterministic strength per event shape (1-5, signals.strength CHECK).
STRENGTH_NEW_PROJECT = 2
STRENGTH_MAJOR_TRANSITION = 3  # into esa_signed or energized
STRENGTH_MINOR_TRANSITION = 2
STRENGTH_BACKWARD = 2
STRENGTH_WITHDRAWAL = 3
STRENGTH_REENTRY = 2
STRENGTH_CAPACITY_CHANGE = 1

#: Rungs whose arrival is materially harder evidence than study progress.
_MAJOR_RUNGS = frozenset({"esa_signed", "energized"})

_INVALIDATION_TEMPLATES = {
    EVENT_NEW_PROJECT: (
        "Invalidated if the project is withdrawn from the queue in a later scan."
    ),
    EVENT_STATUS_TRANSITION: (
        "Invalidated if the project is withdrawn in a later scan, or its status "
        "moves below the previously observed rung again."
    ),
    EVENT_CAPACITY_CHANGE: (
        "Invalidated if a later scan reverts the reported capacity to within "
        f"{CAPACITY_CHANGE_PCT:.0%} of the prior value."
    ),
    EVENT_WITHDRAWAL: (
        "Invalidated if the project re-enters the queue in a later scan."
    ),
}

_CATEGORY = "V2_INTERCONNECTION"


@dataclass(frozen=True)
class QueueTransition:
    """One classified event for one project between two scans."""

    iso: str
    project_id: str
    name: str
    event_type: str
    status_from: str | None
    status_to: str | None
    direction: str  # "positive" | "negative" | "neutral" — signals.direction CHECK
    strength: int  # 1-5 — signals.strength CHECK
    summary_en: str
    summary_zh: str
    capacity_mw: float | None
    capacity_mw_prev: float | None


def classify_pair(prev: QueueProject, curr: QueueProject) -> QueueTransition | None:
    """Classify one project's change between two scans.

    Returns ``None`` when nothing material changed (same rung, capacity within
    tolerance) — idempotence at the pair level. A status move outranks a
    simultaneous capacity move: the transition is the signal, capacity is
    context.
    """
    if prev.iso != curr.iso or prev.project_id != curr.project_id:
        raise ValueError(
            f"classify_pair requires one project: {prev.iso}/{prev.project_id!r} "
            f"vs {curr.iso}/{curr.project_id!r}"
        )

    # A withdrawn project stays dead: no events, not even capacity drift.
    if prev.status_norm == WITHDRAWN and curr.status_norm == WITHDRAWN:
        return None
    if prev.status_norm == WITHDRAWN:
        return _transition(prev, curr, positive=True)  # re-entry after withdrawal
    if curr.status_norm == WITHDRAWN:
        return _withdrawal(prev, curr)

    if prev.status_norm == curr.status_norm:
        return _capacity_event_if_any(prev, curr)

    from_rank = ladder_rank(prev.status_norm)
    to_rank = ladder_rank(curr.status_norm)
    if to_rank > from_rank:
        return _transition(prev, curr, positive=True)
    return _transition(prev, curr, positive=False)  # backward move — still signal


def new_project_event(curr: QueueProject) -> QueueTransition:
    """First sighting of a project in a scan."""
    strength = STRENGTH_NEW_PROJECT
    return QueueTransition(
        iso=curr.iso,
        project_id=curr.project_id,
        name=curr.name,
        event_type=EVENT_NEW_PROJECT,
        status_from=None,
        status_to=curr.status_norm,
        direction="positive",
        strength=strength,
        summary_en=_context(
            f"{curr.iso} queue: new project {curr.name} ({curr.project_id}) "
            f"entered at rung {curr.status_norm.replace('_', ' ')}.",
            curr.capacity_mw,
        ),
        summary_zh=_context_zh(
            f"{curr.iso} 并网队列：新项目 {curr.name}（{curr.project_id}）"
            f"以「{RUNG_ZH[curr.status_norm]}」状态进入队列。",
            curr.capacity_mw,
        ),
        capacity_mw=curr.capacity_mw,
        capacity_mw_prev=None,
    )


def withdrawal_event(prev: QueueProject) -> QueueTransition:
    """A project left the queue — itself the signal, negative by definition."""
    return QueueTransition(
        iso=prev.iso,
        project_id=prev.project_id,
        name=prev.name,
        event_type=EVENT_WITHDRAWAL,
        status_from=prev.status_norm,
        status_to=None,
        direction="negative",
        strength=STRENGTH_WITHDRAWAL,
        summary_en=(
            f"{prev.iso} queue: {prev.name} ({prev.project_id}) left the queue "
            f"from rung {prev.status_norm.replace('_', ' ')}."
        ),
        summary_zh=(
            f"{prev.iso} 并网队列：{prev.name}（{prev.project_id}）"
            f"从「{RUNG_ZH[prev.status_norm]}」状态退出队列。"
        ),
        capacity_mw=prev.capacity_mw,
        capacity_mw_prev=prev.capacity_mw,
    )


def _transition(
    prev: QueueProject, curr: QueueProject, *, positive: bool
) -> QueueTransition:
    if positive:
        strength = (
            STRENGTH_MAJOR_TRANSITION
            if curr.status_norm in _MAJOR_RUNGS
            else STRENGTH_MINOR_TRANSITION
        )
        verb_en = (
            f"progressed from {prev.status_norm.replace('_', ' ')} "
            f"to {curr.status_norm.replace('_', ' ')}"
        )
    else:
        strength = STRENGTH_BACKWARD
        verb_en = (
            f"slipped from {prev.status_norm.replace('_', ' ')} "
            f"back to {curr.status_norm.replace('_', ' ')}"
        )
    return QueueTransition(
        iso=curr.iso,
        project_id=curr.project_id,
        name=curr.name,
        event_type=EVENT_STATUS_TRANSITION,
        status_from=prev.status_norm,
        status_to=curr.status_norm,
        direction="positive" if positive else "negative",
        strength=strength,
        summary_en=_context(
            f"{curr.iso} queue: {curr.name} ({curr.project_id}) {verb_en}.",
            curr.capacity_mw,
        ),
        summary_zh=_context_zh(
            f"{curr.iso} 并网队列：{curr.name}（{curr.project_id}）状态"
            f"由「{RUNG_ZH[prev.status_norm]}」进入「{RUNG_ZH[curr.status_norm]}」。",
            curr.capacity_mw,
        ),
        capacity_mw=curr.capacity_mw,
        capacity_mw_prev=prev.capacity_mw,
    )


def _withdrawal(prev: QueueProject, curr: QueueProject) -> QueueTransition:
    event = withdrawal_event(prev)
    # Carry the curr snapshot's capacity context if the withdrawal row reports it.
    if curr.capacity_mw is not None:
        return replace(event, capacity_mw=curr.capacity_mw)
    return event


def _capacity_event_if_any(prev: QueueProject, curr: QueueProject) -> QueueTransition | None:
    if prev.capacity_mw is None or curr.capacity_mw is None:
        return None
    if prev.capacity_mw <= 0:
        return None
    change = (curr.capacity_mw - prev.capacity_mw) / prev.capacity_mw
    if abs(change) <= CAPACITY_CHANGE_PCT:
        return None
    direction = "positive" if change > 0 else "negative"
    sign_en = "increased" if change > 0 else "decreased"
    sign_zh = "上升" if change > 0 else "下降"
    return QueueTransition(
        iso=curr.iso,
        project_id=curr.project_id,
        name=curr.name,
        event_type=EVENT_CAPACITY_CHANGE,
        status_from=prev.status_norm,
        status_to=curr.status_norm,
        direction=direction,
        strength=STRENGTH_CAPACITY_CHANGE,
        summary_en=(
            f"{curr.iso} queue: {curr.name} ({curr.project_id}) reported capacity "
            f"{sign_en} {abs(change):.0%} to {curr.capacity_mw:,.0f} MW "
            f"(from {prev.capacity_mw:,.0f} MW) — context only, no demand claim."
        ),
        summary_zh=(
            f"{curr.iso} 并网队列：{curr.name}（{curr.project_id}）申报容量"
            f"{sign_zh} {abs(change):.0%}，至 {curr.capacity_mw:,.0f} MW"
            f"（原 {prev.capacity_mw:,.0f} MW）——仅为背景信息，非需求判断。"
        ),
        capacity_mw=curr.capacity_mw,
        capacity_mw_prev=prev.capacity_mw,
    )


def _context(text_en: str, capacity_mw: float | None) -> str:
    if capacity_mw is None:
        return text_en
    return f"{text_en} Reported capacity {capacity_mw:,.0f} MW (context only)."


def _context_zh(text_zh: str, capacity_mw: float | None) -> str:
    if capacity_mw is None:
        return text_zh
    return f"{text_zh} 申报容量 {capacity_mw:,.0f} MW（仅为背景信息）。"


def invalidation_for(event_type: str) -> str:
    """The mandatory invalidation condition for an event type (design doc §4.2)."""
    template = _INVALIDATION_TEMPLATES.get(event_type)
    if template is None:
        raise ValueError(f"no invalidation template for event_type {event_type!r}")
    return template


def to_signal_dict(
    transition: QueueTransition,
    *,
    signal_id: str,
    source_url: str,
    retrieved_at: str,
    theme: str,
) -> dict[str, Any]:
    """Map a classified event onto the canonical signals-table row contract.

    Mirrors ``ingest/edgar.py::_filing_to_signal`` so both pipelines feed the
    store identically. ``verified`` is False: the Verification Agent and the
    human gate run before anything publishes.
    """
    return {
        "signal_id": signal_id,
        "category": _CATEGORY,
        "event_type": transition.event_type,
        "tickers": [],  # ticker mapping is the audited universe-mapping stage
        "theme": theme,
        "iso": transition.iso,
        "project_id": transition.project_id,
        "status_from": transition.status_from,
        "status_to": transition.status_to,
        "summary_en": transition.summary_en,
        "summary_zh": transition.summary_zh,
        "direction": transition.direction,
        "strength": transition.strength,
        "horizon_days": 90,
        "macro_overlay": {"rate_sensitivity": "high", "usd_sensitivity": "low"},
        "invalidation": invalidation_for(transition.event_type),
        "sources": [
            {
                "type": "ISO_queue",
                "url": source_url,
                "retrieved_at": retrieved_at,
            }
        ],
        "verified": False,
    }
