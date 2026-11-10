"""Equal-weight price-return index engine.

Methodology (design doc ``art_y1eOBDsT`` v3.0):

* Equal weight 1/N per constituent at base and at every rebalance; between
  rebalances weights drift with prices (classic equal-weight index math).
* Price return uses raw closes.
* Corporate actions (splits, unusually large special dividends) are detected
  as divergence between the raw-close return and the adjClose return between
  consecutive days. The adjustment is applied through the holdings (the
  divisor equivalent: shares change by the action factor, portfolio value and
  index level stay continuous). This is exact for splits; a special dividend
  is neutralized the same way so the level never gaps. Ordinary cash
  dividends below ``action_threshold`` are left unadjusted — a price-return
  index correctly shows the ex-dividend drop.

Determinism: all arithmetic is plain float math over sorted inputs; runners
round levels at serialization time. Running the engine twice over the same
records produces identical results by construction.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date

logger = logging.getLogger(__name__)

#: Divergence between raw and adjusted returns beyond this relative bound is
#: treated as a split / special dividend; anything smaller is ordinary
#: dividend noise that a price-return index leaves unadjusted.
DEFAULT_ACTION_THRESHOLD = 0.02

BASE_LEVEL = 1000.0


class EngineError(ValueError):
    """Raised when the engine receives inconsistent inputs."""


@dataclass(frozen=True)
class EodRecord:
    """One normalized end-of-day row (raw close plus split/dividend-adjusted close)."""

    date: date
    close: float
    adj_close: float


@dataclass
class IndexState:
    """Holdings-based formulation of the divisor-adjusted equal-weight index.

    ``level = portfolio_value / divisor``; at base, ``divisor`` is set so the
    level is exactly BASE_LEVEL. A rebalance or corporate action rescales the
    divisor (equivalently the holdings) so the level is continuous.
    """

    level: float
    divisor: float
    holdings: dict[str, float]
    last_records: dict[str, EodRecord]


@dataclass(frozen=True)
class IndexEvent:
    """A loud, structured record of every non-routine step."""

    date: date
    kind: str  # "corporate_action" | "rebalance" | "replacement"
    detail: str


@dataclass
class EqualWeightEngine:
    """Equal-weight price-return engine with divisor-style continuity.

    ``composition`` is the constituent ticker list at base. ``prices`` must
    provide an :class:`EodRecord` for every composition member on every date
    presented to :meth:`step` — a missing close is an error, never a silent
    skip (exclusions are decided upstream, loudly).
    """

    composition: list[str]
    tolerance: float = DEFAULT_ACTION_THRESHOLD
    state: IndexState | None = None
    events: list[IndexEvent] = field(default_factory=list)

    def initialize(self, on_date: date, prices: Mapping[str, EodRecord]) -> None:
        """Set the base: level exactly BASE_LEVEL on the base date."""
        missing = [t for t in self.composition if t not in prices]
        if missing:
            raise EngineError(f"base date {on_date} is missing closes for {sorted(missing)}")
        n = len(self.composition)
        if n == 0:
            raise EngineError("refusing to initialize an index with an empty composition")
        # Equal weight = equal dollars per constituent at base: each holding
        # is (1/N) worth of shares at the base close, not 1/N shares.
        holdings = {
            t: (1.0 / n) / prices[t].close for t in self.composition
        }
        value = sum(holdings[t] * prices[t].close for t in self.composition)
        self.state = IndexState(
            level=BASE_LEVEL, divisor=value / BASE_LEVEL,
            holdings=holdings, last_records=dict(prices),
        )

    def step(self, on_date: date, prices: Mapping[str, EodRecord]) -> float:
        """Advance one trading day and return the new level.

        Returns the level rounded to 6 decimals *only as the return value*;
        internal state keeps full precision so chained days stay exact.
        """
        assert self.state is not None, "call initialize() before step()"
        missing = [t for t in self.state.holdings if t not in prices]
        if missing:
            raise EngineError(f"{on_date} is missing closes for {sorted(missing)}")

        for ticker in sorted(self.state.holdings):
            factor = _action_factor(
                self.state.last_records[ticker], prices[ticker], self.tolerance
            )
            if factor != 1.0:
                # Divisor-equivalent adjustment: shares scale by the action
                # factor, so portfolio value and index level stay continuous.
                self.state.holdings[ticker] *= factor
                detail = (
                    f"{ticker}: raw/adjusted return divergence "
                    f"(factor {factor:.6f}); holdings scaled, level continuous"
                )
                self.events.append(IndexEvent(on_date, "corporate_action", detail))
                logger.warning("%s %s", on_date.isoformat(), detail)

        value = sum(h * prices[t].close for t, h in self.state.holdings.items())
        self.state.level = value / self.state.divisor
        self.state.last_records = dict(prices)
        return _round6(self.state.level)

    def rebalance(
        self, on_date: date, prices: Mapping[str, EodRecord], composition: list[str]
    ) -> None:
        """Reset to equal weight over ``composition`` at ``on_date`` closes.

        The level is unchanged by construction: the divisor absorbs the
        difference between the new and old portfolio values. Outgoing
        constituents are valued at their most recent close (today's when
        present, otherwise the last record — logged, since a composition
        change on the rebalance date removes them from ``prices``).
        """
        assert self.state is not None, "call initialize() before rebalance()"
        missing = [t for t in composition if t not in prices]
        if missing:
            raise EngineError(f"rebalance {on_date} is missing closes for {sorted(missing)}")
        old_value = self._portfolio_value(prices)
        n = len(composition)
        if n == 0:
            raise EngineError("refusing to rebalance to an empty composition")
        # Equal weight = equal dollars per constituent at the rebalance close.
        new_holdings = {
            t: (1.0 / n) / prices[t].close for t in composition
        }
        new_value = sum(new_holdings[t] * prices[t].close for t in composition)
        self.state = replace(
            self.state,
            holdings=new_holdings,
            divisor=self.state.divisor * (new_value / old_value),
            last_records=dict(prices),
        )
        self.events.append(
            IndexEvent(
                on_date, "rebalance",
                f"composition reset to {n} constituents; divisor rescaled for continuity",
            )
        )

    def _portfolio_value(self, prices: Mapping[str, EodRecord]) -> float:
        """Value current holdings; fall back to last records for names absent
        from today's price map (outgoing constituents on a rebalance date)."""
        total = 0.0
        for ticker, holdings in self.state.holdings.items():
            record = prices.get(ticker)
            if record is None:
                record = self.state.last_records[ticker]
                logger.warning(
                    "valuing outgoing constituent %s at its last close %s",
                    ticker, record.date,
                )
            total += holdings * record.close
        return total

    def apply_replacement(
        self, on_date: date, prices: Mapping[str, EodRecord], remove: str, add: str
    ) -> None:
        """Swap one constituent (monthly first-Monday review) with level continuity.

        The outgoing position's dollars hand off to the incoming name at
        today's close — value (and therefore the level) is preserved exactly,
        with no divisor change. Assumes no corporate action on ``remove`` on
        the swap date; quarterly rebalances re-equal-weight everything.
        """
        assert self.state is not None, "call initialize() before apply_replacement()"
        if remove not in self.state.holdings:
            raise EngineError(f"replacement: {remove!r} is not a constituent")
        if add in self.state.holdings:
            raise EngineError(f"replacement: {add!r} is already a constituent")
        if add not in prices:
            raise EngineError(f"replacement {on_date} is missing a close for {add!r}")
        if remove in prices:
            close_remove = prices[remove].close
        else:
            # Outgoing names drop out of the day's price map in the pipeline;
            # value them at their last known close, loudly.
            close_remove = self.state.last_records[remove].close
            logger.warning(
                "replacement %s: outgoing constituent %s has no close on the swap "
                "date; valued at last close %s",
                on_date, remove, self.state.last_records[remove].date,
            )
        handoff = self.state.holdings[remove] * close_remove
        holdings = dict(self.state.holdings)
        holdings.pop(remove)
        holdings[add] = handoff / prices[add].close
        self.state = replace(self.state, holdings=holdings, last_records=dict(prices))
        self.events.append(
            IndexEvent(on_date, "replacement", f"{remove} replaced by {add}")
        )


def _action_factor(prev: EodRecord, cur: EodRecord, tolerance: float) -> float:
    """Split / special-dividend factor implied by raw vs adjusted returns.

    Returns 1.0 when the raw-close return matches the adjClose return within
    ``tolerance`` (no corporate action). Otherwise returns the ratio of the
    adjusted return to the raw return — the split factor (or the special
    dividend adjustment) that keeps the index level continuous.

    Both returns are day-over-day: the adjusted return uses the *previous*
    adjClose, not the close, so the factor stays correct even when the
    previous adjClose already differs from the close (an earlier action).
    """
    if prev.close <= 0 or prev.adj_close <= 0:
        raise EngineError(f"non-positive previous record {prev}")
    raw_return = cur.close / prev.close
    if raw_return <= 0:
        raise EngineError(f"non-positive close {cur.close}")
    adj_return = cur.adj_close / prev.adj_close
    factor = adj_return / raw_return
    if abs(factor - 1.0) <= tolerance:
        return 1.0
    return factor


def _round6(value: float) -> float:
    return round(value, 6)
