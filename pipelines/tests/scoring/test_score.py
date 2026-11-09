"""Alpha Score acceptance tests: spec weighting, determinism, zero signals, delta ranking.

Every expected score below is hand-computed from the published weights and
saturation constants (see the arithmetic in each test) so the fixture pins the
spec, not the implementation.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pipelines.scoring import (
    FUNDAMENTALS_WEIGHT,
    MACRO_WEIGHT,
    MOMENTUM_SATURATION,
    MOMENTUM_WEIGHT,
    V2_V4_BOOST,
    VALUATION_WEIGHT,
    VERTICAL_SATURATION,
    VERTICAL_WEIGHT,
    WINDOW_DAYS,
    AlphaScore,
    SignalEvent,
    alpha_score,
    score_companies,
    score_deltas,
)

AS_OF = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
TICKERS = ["GEV", "PWR", "NEE", "UNCOVERED"]


def event(
    signal_id: str,
    *,
    category: str = "G6_EARNINGS",
    direction: str = "positive",
    strength: int = 3,
    age_days: float = 0.0,
    tickers: tuple[str, ...] = ("GEV",),
) -> SignalEvent:
    published = datetime.fromtimestamp(
        AS_OF.timestamp() - age_days * 86_400, tz=UTC
    )
    return SignalEvent(
        signal_id=signal_id,
        category=category,
        direction=direction,
        strength=strength,
        published_at=published,
        tickers=tickers,
    )


def expected_score(momentum_dim: float, vertical_dim: float) -> float:
    """Score from dimensions with fundamentals/valuation/macro at neutral."""
    neutral = (FUNDAMENTALS_WEIGHT + VALUATION_WEIGHT + MACRO_WEIGHT) * 0.5
    return 100.0 * (MOMENTUM_WEIGHT * momentum_dim + VERTICAL_WEIGHT * vertical_dim + neutral)


def test_weight_sum_is_one() -> None:
    assert (
        MOMENTUM_WEIGHT + VERTICAL_WEIGHT + FUNDAMENTALS_WEIGHT + VALUATION_WEIGHT
        + MACRO_WEIGHT
    ) == pytest.approx(1.0)


def test_zero_signal_company_scores_neutral() -> None:
    score = alpha_score([], "UNCOVERED", AS_OF)
    assert score.score == pytest.approx(50.0)
    assert score.momentum == pytest.approx(0.5)
    assert score.vertical == pytest.approx(0.5)


def test_single_vertical_v2_signal_hand_computed() -> None:
    # Positive strength-5 V2 event, age 0: raw = 5 * 1.0 * V2_V4_BOOST = 7.5.
    # momentum_dim = 0.5 + 7.5/(2*8) = 0.96875; vertical_dim saturates at 1.0.
    events = [event("SIG-V2", category="V2_INTERCONNECTION", strength=5)]
    score = alpha_score(events, "GEV", AS_OF)
    raw = 5.0 * V2_V4_BOOST
    expected_momentum = min(1.0, 0.5 + raw / (2 * MOMENTUM_SATURATION))
    expected_vertical = min(1.0, 0.5 + raw / (2 * VERTICAL_SATURATION))
    assert score.score == pytest.approx(expected_score(expected_momentum, expected_vertical))
    assert score.score == pytest.approx(78.90625)
    assert score.momentum_raw == pytest.approx(raw)
    assert score.vertical_raw == pytest.approx(raw)


def test_general_signal_recency_decay_hand_computed() -> None:
    # Positive strength-4 G6 event, 45 days old (half the 90-day window):
    # decay = 1 - 45/90 = 0.5, raw = 4 * 0.5 = 2.0.
    # momentum_dim = 0.5 + 2/16 = 0.625; vertical_dim stays neutral at 0.5.
    events = [event("SIG-G6", strength=4, age_days=45.0)]
    score = alpha_score(events, "GEV", AS_OF)
    assert score.score == pytest.approx(expected_score(0.625, 0.5))
    assert score.score == pytest.approx(54.375)


def test_negative_signal_pulls_score_below_neutral() -> None:
    # Negative strength-3 event, age 0: raw = -3, momentum_dim = 0.5 - 3/16.
    events = [event("SIG-NEG", direction="negative", strength=3)]
    score = alpha_score(events, "GEV", AS_OF)
    assert score.score == pytest.approx(expected_score(0.5 - 3.0 / (2 * MOMENTUM_SATURATION), 0.5))
    assert score.score < 50.0


def test_signals_outside_window_do_not_contribute() -> None:
    stale = [event("SIG-OLD", strength=5, age_days=WINDOW_DAYS + 1)]
    fresh = [event("SIG-NEW", strength=5, age_days=WINDOW_DAYS - 1)]
    assert alpha_score(stale, "GEV", AS_OF).score == pytest.approx(50.0)
    assert alpha_score(fresh, "GEV", AS_OF).score > 50.0


def test_future_events_are_invisible_at_as_of() -> None:
    tomorrow = [
        SignalEvent(
            signal_id="SIG-FUT",
            category="V4_DATACENTER",
            direction="positive",
            strength=5,
            published_at=AS_OF.fromtimestamp(AS_OF.timestamp() + 86_400, tz=UTC),
            tickers=("GEV",),
        )
    ]
    assert alpha_score(tomorrow, "GEV", AS_OF).score == pytest.approx(50.0)


def test_neutral_direction_does_not_move_score() -> None:
    events = [event("SIG-NEUT", direction="neutral", strength=5)]
    assert alpha_score(events, "GEV", AS_OF).score == pytest.approx(50.0)


def test_events_route_only_to_their_own_tickers() -> None:
    events = [event("SIG-PWR", tickers=("PWR",), strength=5)]
    scores = score_companies(events, TICKERS, AS_OF)
    assert scores["GEV"].score == pytest.approx(50.0)
    assert scores["PWR"].score > 50.0
    assert scores["UNCOVERED"].score == pytest.approx(50.0)


def test_same_inputs_same_score() -> None:
    events = [
        event("SIG-A", category="V2_INTERCONNECTION", strength=5),
        event("SIG-B", direction="negative", strength=2, age_days=10.0),
        event("SIG-C", strength=3, age_days=30.0, tickers=("GEV", "PWR")),
    ]
    first = alpha_score(events, "GEV", AS_OF)
    assert alpha_score(list(events), "GEV", AS_OF) == first
    # Reordering the same inputs changes only float summation noise, not the score.
    reordered = alpha_score(list(reversed(events)), "GEV", AS_OF)
    assert reordered.score == pytest.approx(first.score)
    assert reordered.momentum == pytest.approx(first.momentum)
    assert reordered.vertical == pytest.approx(first.vertical)


def test_score_is_bounded() -> None:
    events = [event(f"SIG-{i}", category="V2_INTERCONNECTION", strength=5) for i in range(50)]
    events += [
        event(f"SIGN-{i}", category="V4_DATACENTER", direction="negative", strength=5)
        for i in range(50)
    ]
    score = alpha_score(events, "GEV", AS_OF)
    assert 0.0 <= score.score <= 100.0


def test_delta_ordering_ranks_movers_first() -> None:
    # GEV gains a strong V2 signal mid-window; NEE loses one; PWR is unchanged.
    events = [
        event("SIG-GEV", category="V2_INTERCONNECTION", strength=5, tickers=("GEV",)),
        event(
            "SIG-NEE",
            category="V2_INTERCONNECTION",
            direction="negative",
            strength=4,
            age_days=3.0,
            tickers=("NEE",),
        ),
    ]
    deltas = score_deltas(events, TICKERS, AS_OF, window_days=7)
    # Ties (delta 0.0) break alphabetically: PWR before UNCOVERED.
    assert [d.ticker for d in deltas] == ["GEV", "PWR", "UNCOVERED", "NEE"]
    assert deltas[0].delta > 0
    assert deltas[0].previous == pytest.approx(50.0)
    assert deltas[-1].delta < 0
    assert deltas[1].delta == pytest.approx(0.0) and deltas[2].delta == pytest.approx(0.0)


def test_from_row_builds_from_signals_table_columns() -> None:
    row = {
        "signal_id": "SIG-2026-11-000001",
        "category": "V2_INTERCONNECTION",
        "direction": "positive",
        "strength": 4,
        "published_at": AS_OF,
        "tickers": ["GEV", "PWR"],
        "event_type": "status_transition",
    }
    event_from_row = SignalEvent.from_row(row)
    assert event_from_row.tickers == ("GEV", "PWR")
    assert alpha_score([event_from_row], "PWR", AS_OF).score > 50.0


def test_invalid_event_inputs_fail_loudly() -> None:
    with pytest.raises(ValueError, match="direction"):
        event("SIG-BAD", direction="bullish")
    with pytest.raises(ValueError, match="strength"):
        event("SIG-BAD", strength=6)
    with pytest.raises(ValueError, match="timezone-aware"):
        SignalEvent(
            signal_id="SIG-NAIVE",
            category="G6_EARNINGS",
            direction="positive",
            strength=3,
            published_at=datetime(2026, 11, 9, 12, 0),
        )


def test_alpha_score_type_export() -> None:
    assert isinstance(alpha_score([], "GEV", AS_OF), AlphaScore)
