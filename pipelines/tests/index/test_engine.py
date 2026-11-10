"""Tests for the equal-weight index engine.

Synthetic fixtures only — no network, no market data. The continuity
contract: a split or large special dividend must leave the index level
unchanged on the action day.
"""

from __future__ import annotations

from datetime import date

import pytest

from pipelines.indices.engine import (
    BASE_LEVEL,
    EngineError,
    EodRecord,
    EqualWeightEngine,
    _action_factor,
)

D1 = date(2027, 3, 30)
D2 = date(2027, 3, 31)
D3 = date(2027, 4, 1)


def _rec(d: date, close: float, adj: float | None = None) -> EodRecord:
    return EodRecord(d, close, close if adj is None else adj)


def _two_ticker_engine(tolerance: float = 0.02) -> EqualWeightEngine:
    engine = EqualWeightEngine(composition=["AAA", "BBB"], tolerance=tolerance)
    engine.initialize(D1, {"AAA": _rec(D1, 100.0), "BBB": _rec(D1, 200.0)})
    return engine


class TestBaseDate:
    def test_base_level_is_exactly_1000(self) -> None:
        engine = _two_ticker_engine()
        assert engine.state is not None
        assert engine.state.level == 1000.0

    def test_missing_base_close_is_loud(self) -> None:
        engine = EqualWeightEngine(composition=["AAA", "BBB"])
        with pytest.raises(EngineError, match="BBB"):
            engine.initialize(D1, {"AAA": _rec(D1, 100.0)})

    def test_empty_composition_refused(self) -> None:
        engine = EqualWeightEngine(composition=[])
        with pytest.raises(EngineError, match="empty composition"):
            engine.initialize(D1, {})

    def test_plain_step_math(self) -> None:
        engine = _two_ticker_engine()
        level = engine.step(D2, {"AAA": _rec(D2, 110.0), "BBB": _rec(D2, 200.0)})
        # AAA +10%, BBB flat, equal weight -> +5% overall.
        assert level == pytest.approx(1050.0, abs=1e-6)


class TestSplitContinuity:
    def test_two_for_one_split_leaves_level_unchanged(self) -> None:
        """A 2:1 split must not move the index: level with the split equals
        the level computed from the split-adjusted price directly."""
        split_engine = _two_ticker_engine()
        # AAA splits 2:1: raw close 60, adjusted 120 (raw x split factor).
        split_engine.step(D2, {"AAA": _rec(D2, 60.0, adj=120.0), "BBB": _rec(D2, 200.0)})
        level_with_split = split_engine.state.level

        no_split_engine = _two_ticker_engine()
        no_split_engine.step(D2, {"AAA": _rec(D2, 120.0), "BBB": _rec(D2, 200.0)})
        level_without_split = no_split_engine.state.level

        assert level_with_split == pytest.approx(level_without_split, rel=1e-12)

    def test_split_is_logged_loudly(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        engine = _two_ticker_engine()
        with caplog.at_level(logging.WARNING):
            engine.step(D2, {"AAA": _rec(D2, 60.0, adj=120.0), "BBB": _rec(D2, 200.0)})
        assert "AAA" in caplog.text and "divergence" in caplog.text
        kinds = [e.kind for e in engine.events]
        assert "corporate_action" in kinds

    def test_three_for_one_split_holds_next_day(self) -> None:
        """Continuity persists: after the split, returns compute off the new share count."""
        engine = _two_ticker_engine()
        # Pure 3:1 split on AAA: raw close 100/3, adjusted 100 (no price move).
        engine.step(D2, {"AAA": _rec(D2, 100.0 / 3.0, adj=100.0), "BBB": _rec(D2, 200.0)})
        level_d2 = engine.state.level
        assert level_d2 == pytest.approx(1000.0, abs=1e-6)
        # Day 3: AAA +5% (35.0 = (100/3) * 1.05, adjusted 105), BBB flat.
        engine.step(D3, {"AAA": _rec(D3, 35.0, adj=105.0), "BBB": _rec(D3, 200.0)})
        level_d3 = engine.state.level
        # AAA 5% up on day 3, BBB flat, equal weight -> +2.5%.
        assert level_d3 == pytest.approx(level_d2 * 1.025, rel=1e-9)

    def test_ordinary_dividend_is_not_adjusted(self) -> None:
        """A 1% raw/adjusted divergence is under the threshold: the price
        index shows the ex-dividend drop (price return, not total return)."""
        engine = _two_ticker_engine()
        # AAA closes at 99 with adjusted close 100 -> 1% divergence.
        engine.step(D2, {"AAA": _rec(D2, 99.0, adj=100.0), "BBB": _rec(D2, 200.0)})
        level = engine.state.level
        # No-action reference: AAA at raw 99, BBB 200.
        reference = _two_ticker_engine()
        reference.step(D2, {"AAA": _rec(D2, 99.0), "BBB": _rec(D2, 200.0)})
        assert level == pytest.approx(reference.state.level, rel=1e-12)


class TestSpecialDividend:
    def test_large_special_dividend_keeps_level_continuous(self) -> None:
        """$25 special dividend on a $100 stock: raw close 75, adjusted 100.
        The divisor-style adjustment must keep the level where it would be
        without the dividend."""
        div_engine = _two_ticker_engine()
        div_engine.step(D2, {"AAA": _rec(D2, 75.0, adj=100.0), "BBB": _rec(D2, 200.0)})
        level_with = div_engine.state.level

        no_div_engine = _two_ticker_engine()
        no_div_engine.step(D2, {"AAA": _rec(D2, 100.0), "BBB": _rec(D2, 200.0)})
        level_without = no_div_engine.state.level

        assert level_with == pytest.approx(level_without, rel=1e-12)


class TestActionFactor:
    def test_no_action_when_raw_and_adj_match(self) -> None:
        prev = _rec(D1, 100.0)
        cur = _rec(D2, 105.0)
        assert _action_factor(prev, cur, 0.02) == 1.0

    def test_factor_correct_when_prev_adj_differs(self) -> None:
        """After an earlier action, prev adjClose != close; the factor uses
        the previous adjClose, so a second split is still detected exactly."""
        prev = _rec(D1, 60.0, adj=120.0)
        cur = _rec(D2, 50.0, adj=200.0)  # another 5:3-ish move; ratio check below
        # raw 50/60, adj 200/120 -> factor = (200/120)/(50/60) = 2.0
        assert _action_factor(prev, cur, 0.02) == pytest.approx(2.0)

    def test_non_positive_close_refused(self) -> None:
        with pytest.raises(EngineError, match="non-positive"):
            _action_factor(_rec(D1, 100.0), _rec(D2, 0.0), 0.02)


class TestRebalance:
    def test_rebalance_keeps_level_and_resets_weights(self) -> None:
        engine = _two_ticker_engine()
        level_before = engine.step(D2, {"AAA": _rec(D2, 150.0), "BBB": _rec(D2, 100.0)})

        engine.rebalance(D2, {"AAA": _rec(D2, 150.0), "BBB": _rec(D2, 100.0)}, ["AAA", "BBB"])
        assert engine.state.level == pytest.approx(level_before, rel=1e-12)
        # Equal dollars: each holding's value is exactly 1/N of the portfolio.
        for ticker, h in engine.state.holdings.items():
            assert h * engine.state.last_records[ticker].close == pytest.approx(0.5)

    def test_rebalance_to_new_composition(self) -> None:
        engine = _two_ticker_engine()
        engine.step(D2, {"AAA": _rec(D2, 150.0), "BBB": _rec(D2, 100.0)})
        level_before = engine.state.level
        prices = {"AAA": _rec(D2, 150.0), "CCC": _rec(D2, 50.0)}
        engine.rebalance(D2, prices, ["AAA", "CCC"])
        assert set(engine.state.holdings) == {"AAA", "CCC"}
        assert engine.state.level == pytest.approx(level_before, rel=1e-12)

    def test_missing_rebalance_price_is_loud(self) -> None:
        engine = _two_ticker_engine()
        with pytest.raises(EngineError, match="missing closes"):
            engine.rebalance(D2, {"AAA": _rec(D2, 150.0)}, ["AAA", "BBB"])

    def test_level_moves_correctly_after_rebalance(self) -> None:
        engine = _two_ticker_engine()
        engine.step(D2, {"AAA": _rec(D2, 150.0), "BBB": _rec(D2, 100.0)})
        engine.rebalance(D2, {"AAA": _rec(D2, 150.0), "BBB": _rec(D2, 100.0)}, ["AAA", "BBB"])
        level_after_rebalance = engine.state.level
        engine.step(D3, {"AAA": _rec(D3, 150.0), "BBB": _rec(D3, 110.0)})
        # Re-equal-weighted, so a 10% BBB move lifts the index exactly 5%.
        assert engine.state.level == pytest.approx(level_after_rebalance * 1.05, rel=1e-9)


class TestReplacement:
    def test_replacement_swaps_and_stays_continuous(self) -> None:
        engine = _two_ticker_engine()
        engine.step(D2, {"AAA": _rec(D2, 150.0), "BBB": _rec(D2, 100.0)})
        level_before = engine.state.level
        prices = {"AAA": _rec(D2, 150.0), "CCC": _rec(D2, 100.0)}
        engine.apply_replacement(D2, prices, "BBB", "CCC")
        assert set(engine.state.holdings) == {"AAA", "CCC"}
        assert engine.state.level == pytest.approx(level_before, rel=1e-12)
        kinds = [(e.kind, e.detail) for e in engine.events]
        assert any(k == "replacement" and "BBB replaced by CCC" in d for k, d in kinds)

    def test_replacing_absent_ticker_is_loud(self) -> None:
        engine = _two_ticker_engine()
        with pytest.raises(EngineError, match="not a constituent"):
            engine.apply_replacement(D2, {}, "ZZZ", "AAA")

    def test_base_level_constant(self) -> None:
        assert BASE_LEVEL == 1000.0
