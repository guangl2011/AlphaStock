"""Deterministic momentum-weighted Alpha Score (0-100).

Spec: pinned Blueprint art_jLZaGljO ("Alpha Score (signal-momentum-weighted
MVP)") and design doc §5. The score is a pure function of the signal events
visible for a ticker at a reference time — no clock reads, no network, no
model state — so identical inputs always produce an identical score.

MVP weights (design doc §5):

    signal momentum 35% | vertical edge 25% | fundamentals 20%
    valuation 10%       | macro fit 10%

Fundamentals, valuation, and macro fit have no vendor feed in the MVP and read
as neutral until one is chosen (spec: "fundamentals weight can read as neutral
until a vendor is chosen"). Momentum and vertical edge are computed from the
signal events only.

Each computed dimension is normalized to 0..1 around a neutral 0.5, so a
company with zero signals scores exactly 50 — absence of evidence is not
evidence of absence. Raw momentum saturates linearly at the named saturation
constants rather than passing through a learned or probabilistic model; there
is no black-box drift by construction.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

SECONDS_PER_DAY = 86_400.0

# Design doc §5 weights. They must sum to 1.0 — guarded by test_weight_sum.
MOMENTUM_WEIGHT = 0.35
VERTICAL_WEIGHT = 0.25
FUNDAMENTALS_WEIGHT = 0.20
VALUATION_WEIGHT = 0.10
MACRO_WEIGHT = 0.10

# A dimension with no information reads as neutral.
NEUTRAL_DIMENSION = 0.5

# Only signal events inside this window contribute (Blueprint: "recency-decay
# over 90 days"); linear decay reaches zero at the window edge.
WINDOW_DAYS = 90.0

# Raw momentum of ±SATURATION drives its dimension fully to 1.0/0.0. The
# vertical scale is smaller because proprietary (V1-V4) signals are rarer, so
# a given event moves the vertical dimension further than the momentum one.
MOMENTUM_SATURATION = 8.0
VERTICAL_SATURATION = 3.0

# Blueprint: "vertical-signal boost for V2/V4".
V2_V4_BOOST = 1.5

_VERTICAL_PREFIXES = ("V1", "V2", "V3", "V4")
_DIRECTION_SIGNS = {"positive": 1.0, "negative": -1.0, "neutral": 0.0}


@dataclass(frozen=True)
class SignalEvent:
    """One signal as stored in the `signals` table (db/migrations/0001_signal_store.sql)."""

    signal_id: str
    category: str
    direction: str  # "positive" | "negative" | "neutral" — signals.direction CHECK
    strength: int  # 1-5 — signals.strength CHECK
    published_at: datetime  # must be timezone-aware, matching timestamptz
    tickers: tuple[str, ...] = ()
    event_type: str = "other"

    def __post_init__(self) -> None:
        if self.direction not in _DIRECTION_SIGNS:
            raise ValueError(f"invalid direction: {self.direction!r}")
        if not 1 <= self.strength <= 5:
            raise ValueError(f"strength must be 1-5, got {self.strength}")
        if self.published_at.tzinfo is None:
            raise ValueError(
                "published_at must be timezone-aware (signals.published_at is timestamptz)"
            )

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> SignalEvent:
        """Build from a `signals` table row keyed by the migration's column names."""
        tickers = row["tickers"]
        return cls(
            signal_id=str(row["signal_id"]),
            category=str(row["category"]),
            direction=str(row["direction"]),
            strength=int(row["strength"]),  # type: ignore[call-overload]
            published_at=row["published_at"],  # type: ignore[arg-type]
            tickers=tuple(tickers) if isinstance(tickers, (list, tuple, set)) else (),
            event_type=str(row.get("event_type", "other")),
        )


@dataclass(frozen=True)
class AlphaScore:
    """Score with its exposed dimensions — the score is never a black box."""

    ticker: str
    score: float
    momentum: float  # 0..1, 0.5 neutral
    vertical: float  # 0..1, 0.5 neutral
    momentum_raw: float
    vertical_raw: float


@dataclass(frozen=True)
class ScoreDelta:
    """Score now vs. `window_days` ago for one ticker."""

    ticker: str
    score: float
    previous: float
    delta: float


def _is_vertical(category: str) -> bool:
    return category.split("_", 1)[0] in _VERTICAL_PREFIXES


def _is_v2_or_v4(category: str) -> bool:
    return category.split("_", 1)[0] in ("V2", "V4")


def _clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))


def _dimension(raw: float, saturation: float) -> float:
    """Map a raw weighted sum to 0..1 around neutral 0.5, saturating linearly."""
    return _clamp01(NEUTRAL_DIMENSION + raw / (2.0 * saturation))


def _decay(published_at: datetime, as_of: datetime) -> float:
    """Linear recency decay over the window; events in the future never reach here."""
    age_days = (as_of - published_at).total_seconds() / SECONDS_PER_DAY
    return max(0.0, 1.0 - age_days / WINDOW_DAYS)


def _company_events(
    events: Iterable[SignalEvent], ticker: str, as_of: datetime
) -> list[SignalEvent]:
    return [
        e
        for e in events
        if ticker in e.tickers and e.published_at <= as_of
    ]


def alpha_score(events: Iterable[SignalEvent], ticker: str, as_of: datetime) -> AlphaScore:
    """Alpha Score (0-100) for one ticker from the signals visible at `as_of`.

    Pure function: no clock reads, no mutation. A company with zero visible
    signals scores exactly 50.0 (all dimensions neutral).
    """
    visible = _company_events(events, ticker, as_of)
    momentum_raw = 0.0
    vertical_raw = 0.0
    momentum_decayed = 0.0
    vertical_decayed = 0.0
    for event in visible:
        weighted = _DIRECTION_SIGNS[event.direction] * event.strength
        if _is_vertical(event.category):
            weighted *= V2_V4_BOOST if _is_v2_or_v4(event.category) else 1.0
            vertical_raw += weighted
        momentum_raw += weighted
        decayed = weighted * _decay(event.published_at, as_of)
        if _is_vertical(event.category):
            vertical_decayed += decayed
        momentum_decayed += decayed

    momentum = _dimension(momentum_decayed, MOMENTUM_SATURATION)
    vertical = _dimension(vertical_decayed, VERTICAL_SATURATION)
    neutral_total = (
        FUNDAMENTALS_WEIGHT + VALUATION_WEIGHT + MACRO_WEIGHT
    ) * NEUTRAL_DIMENSION
    score = 100.0 * (
        MOMENTUM_WEIGHT * momentum + VERTICAL_WEIGHT * vertical + neutral_total
    )
    return AlphaScore(
        ticker=ticker,
        score=score,
        momentum=momentum,
        vertical=vertical,
        momentum_raw=momentum_raw,
        vertical_raw=vertical_raw,
    )


def score_companies(
    events: Iterable[SignalEvent], tickers: Sequence[str], as_of: datetime
) -> dict[str, AlphaScore]:
    """Score every covered ticker; companies with zero signals score neutral."""
    event_list = list(events)
    return {ticker: alpha_score(event_list, ticker, as_of) for ticker in tickers}


def score_deltas(
    events: Iterable[SignalEvent],
    tickers: Sequence[str],
    as_of: datetime,
    window_days: int = 7,
) -> list[ScoreDelta]:
    """Score change over `window_days`, ranked best-first (movers first).

    The previous score is computed as of `as_of - window_days`, so an event
    published mid-window counts fully in the current score and not at all in
    the previous one. Deterministic: ties break by ticker for a stable order.
    """
    event_list = list(events)
    previous_as_of = as_of - timedelta(days=window_days)
    deltas = []
    for ticker in tickers:
        score_now = alpha_score(event_list, ticker, as_of).score
        score_prev = alpha_score(event_list, ticker, previous_as_of).score
        deltas.append(ScoreDelta(ticker, score_now, score_prev, score_now - score_prev))
    return sorted(deltas, key=lambda d: (-d.delta, d.ticker))
