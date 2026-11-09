"""Transition-grammar tests: ladder order, event classification, signal contract.

The grammar is pure pair logic — tests construct QueueProject rows directly,
no fixtures, no network. Cases cover every branch the spec's event detector
names: forward progress, backward moves (allowed, still signal), withdrawal,
re-entry, capacity as de-weighted context, and the canonical signal-row
contract shared with the V4 pipeline.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pipelines.ingest.queues.base import QueueProject
from pipelines.ingest.queues.ladder import CANONICAL_ORDER, WITHDRAWN, ladder_rank
from pipelines.signals.transitions import (
    CAPACITY_CHANGE_PCT,
    EVENT_CAPACITY_CHANGE,
    EVENT_NEW_PROJECT,
    EVENT_STATUS_TRANSITION,
    EVENT_WITHDRAWAL,
    QueueTransition,
    classify_pair,
    invalidation_for,
    new_project_event,
    to_signal_dict,
    withdrawal_event,
)

RETRIEVED = datetime(2026, 11, 9, 6, 0, tzinfo=UTC)


def project(
    status: str = "requested",
    capacity: float | None = 100.0,
    pid: str = "PJM-0001",
    iso: str = "PJM",
    name: str = "Test Project",
) -> QueueProject:
    return QueueProject(
        iso=iso,
        project_id=pid,
        name=name,
        capacity_mw=capacity,
        load_type="unknown",
        data_center_likelihood=0.0,
        status=status,  # native label; the grammar reads status_norm only
        status_norm=status,
        county=None,
        state=None,
        raw={},
        source_url=f"https://example.com/{iso.lower()}",
        retrieved_at=RETRIEVED,
    )


class TestLadder:
    def test_ladder_order_is_canonical(self) -> None:
        ranks = [ladder_rank(s) for s in CANONICAL_ORDER]
        assert ranks == sorted(ranks)

    def test_withdrawn_ranks_below_everything(self) -> None:
        assert ladder_rank(WITHDRAWN) == -1
        assert ladder_rank(WITHDRAWN) < ladder_rank("requested")

    def test_non_canonical_status_fails(self) -> None:
        with pytest.raises(ValueError, match="not a canonical ladder status"):
            ladder_rank("in_queue")


class TestClassifyPair:
    def test_unchanged_pair_yields_none(self) -> None:
        assert classify_pair(project(), project()) is None

    def test_forward_transition_requested_to_studied(self) -> None:
        event = classify_pair(project("requested"), project("studied"))
        assert event is not None
        assert event.event_type == EVENT_STATUS_TRANSITION
        assert (event.status_from, event.status_to) == ("requested", "studied")
        assert event.direction == "positive"
        assert event.strength == 2

    def test_full_ladder_walk_is_forward_positive(self) -> None:
        current = project("requested")
        for rung in CANONICAL_ORDER[1:]:
            nxt = project(rung)
            event = classify_pair(current, nxt)
            assert event is not None and event.direction == "positive"
            assert event.status_to == rung
            current = nxt

    def test_arrival_at_esa_signed_is_major_strength(self) -> None:
        event = classify_pair(project("approved"), project("esa_signed"))
        assert event is not None and event.strength == 3

    def test_arrival_at_energized_is_major_strength(self) -> None:
        event = classify_pair(project("esa_signed"), project("energized"))
        assert event is not None and event.strength == 3

    def test_backward_move_is_a_negative_event(self) -> None:
        event = classify_pair(project("approved"), project("studied"))
        assert event is not None
        assert event.event_type == EVENT_STATUS_TRANSITION
        assert event.direction == "negative"
        assert event.strength == 2
        assert (event.status_from, event.status_to) == ("approved", "studied")

    def test_withdrawal_event(self) -> None:
        event = classify_pair(project("studied"), project("withdrawn"))
        assert event is not None
        assert event.event_type == EVENT_WITHDRAWAL
        assert event.direction == "negative"
        assert event.strength == 3
        assert event.status_to is None
        assert event.status_from == "studied"

    def test_reentry_after_withdrawal_is_positive_transition(self) -> None:
        event = classify_pair(project("withdrawn"), project("requested"))
        assert event is not None
        assert event.event_type == EVENT_STATUS_TRANSITION
        assert event.direction == "positive"

    def test_withdrawn_projects_stay_dead(self) -> None:
        # Capacity drift on a dead project is not signal.
        assert classify_pair(project("withdrawn", 100.0), project("withdrawn", 500.0)) is None

    def test_status_move_outranks_capacity_change(self) -> None:
        event = classify_pair(project("studied", 100.0), project("esa_signed", 200.0))
        assert event is not None
        assert event.event_type == EVENT_STATUS_TRANSITION  # not capacity_change

    def test_pair_identity_enforced(self) -> None:
        with pytest.raises(ValueError, match="requires one project"):
            classify_pair(project(pid="A"), project(pid="B"))
        with pytest.raises(ValueError, match="requires one project"):
            classify_pair(project(iso="PJM"), project(iso="MISO"))


class TestCapacityContext:
    def test_material_capacity_change_is_a_context_event(self) -> None:
        event = classify_pair(project("studied", 150.0), project("studied", 200.0))
        assert event is not None
        assert event.event_type == EVENT_CAPACITY_CHANGE
        assert event.direction == "positive"
        assert event.strength == 1  # de-weighted: context, not demand

    def test_small_capacity_change_is_ignored(self) -> None:
        just_under = 150.0 * (1 + CAPACITY_CHANGE_PCT) - 0.01
        assert classify_pair(project("studied", 150.0), project("studied", just_under)) is None

    def test_missing_capacity_never_events(self) -> None:
        assert classify_pair(project("studied", None), project("studied", 300.0)) is None

    def test_capacity_down_is_negative(self) -> None:
        event = classify_pair(project("studied", 200.0), project("studied", 100.0))
        assert event is not None and event.direction == "negative"

    def test_capacity_mentioned_as_context_only(self) -> None:
        event = classify_pair(project("studied", 150.0), project("studied", 300.0))
        assert event is not None
        assert "context only" in event.summary_en
        assert "背景信息" in event.summary_zh


class TestBilingualSummaries:
    def test_transition_summaries_name_both_rungs(self) -> None:
        event = classify_pair(project("studied"), project("esa_signed"))
        assert event is not None
        assert "studied" in event.summary_en and "esa signed" in event.summary_en
        assert "并网队列" in event.summary_zh
        assert "研究" in event.summary_zh and "签署并网协议" in event.summary_zh

    def test_withdrawal_summaries(self) -> None:
        event = withdrawal_event(project("studied"))
        assert "left the queue" in event.summary_en
        assert "退出队列" in event.summary_zh

    def test_new_project_summaries(self) -> None:
        event = new_project_event(project("requested", 250.0))
        assert "new project" in event.summary_en
        assert "新项目" in event.summary_zh
        assert event.event_type == EVENT_NEW_PROJECT
        assert event.status_from is None
        assert event.direction == "positive"


class TestSignalRowContract:
    """Every event maps onto the canonical signals-table row (V4 parity)."""

    def test_every_event_type_has_invalidation(self) -> None:
        for event_type in (
            EVENT_NEW_PROJECT,
            EVENT_STATUS_TRANSITION,
            EVENT_CAPACITY_CHANGE,
            EVENT_WITHDRAWAL,
        ):
            text = invalidation_for(event_type)
            assert text.strip()

    def test_unknown_event_type_has_no_invalidation(self) -> None:
        with pytest.raises(ValueError, match="no invalidation template"):
            invalidation_for("other")

    SIGNAL_KEYS = {
        "signal_id",
        "category",
        "event_type",
        "tickers",
        "theme",
        "iso",
        "project_id",
        "status_from",
        "status_to",
        "summary_en",
        "summary_zh",
        "direction",
        "strength",
        "horizon_days",
        "macro_overlay",
        "invalidation",
        "sources",
        "verified",
    }

    def test_to_signal_dict_shape(self) -> None:
        transition = classify_pair(project("studied"), project("esa_signed"))
        assert isinstance(transition, QueueTransition)
        row = to_signal_dict(
            transition,
            signal_id="SIG-20261109-abcdef12",
            source_url="https://example.com/pjm",
            retrieved_at="2026-11-09T06:00:00Z",
            theme="Grid & Transmission",
        )
        assert set(row) == self.SIGNAL_KEYS
        assert row["category"] == "V2_INTERCONNECTION"
        assert row["verified"] is False  # verification + human gate come later
        assert row["tickers"] == []  # ticker mapping is the audited later stage
        assert row["invalidation"] == invalidation_for(EVENT_STATUS_TRANSITION)
        source = row["sources"][0]
        assert source["type"] == "ISO_queue"
        assert source["url"] == "https://example.com/pjm"
        assert source["retrieved_at"] == "2026-11-09T06:00:00Z"
        assert row["macro_overlay"] == {
            "rate_sensitivity": "high",
            "usd_sensitivity": "low",
        }
