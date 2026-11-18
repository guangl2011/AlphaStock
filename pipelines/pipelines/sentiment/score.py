"""Deterministic finance-tuned lexicon sentiment scorer.

Constraints from the blueprint's scoring decision:

* No LLM in the scoring path — a curated lexicon only, so every scan is
  reproducible and auditable.
* No network and no clock: ``score_text`` is a pure function of its input.
  The same text always yields the same score, on any machine, at any time.

Scoring walks the token list once. Each lexicon term's weight is adjusted by
its local context: intensity modifiers within the three tokens before the term
scale it, and a negator in that window flips its sign. The total is squashed
through tanh into [-1, +1].
"""

from __future__ import annotations

import math
import re

TOKEN_RE = re.compile(r"[a-z']+")

# Context window (tokens before a term) checked for negators and intensifiers.
CONTEXT_WINDOW = 3

NEGATORS: frozenset[str] = frozenset(
    {
        "not",
        "no",
        "never",
        "isn't",
        "aren't",
        "wasn't",
        "won't",
        "doesn't",
        "didn't",
        "can't",
        "cannot",
        "without",
        "hard",
        "unlikely",
        "fails",
        "failing",
        "fail",
        "failed",
        "despite",
        "barely",
    }
)

INTENSIFIERS: dict[str, float] = {
    "very": 1.5,
    "extremely": 1.8,
    "massively": 1.8,
    "sharply": 1.6,
    "significantly": 1.4,
    "hugely": 1.6,
    "record": 1.5,
    "surging": 1.4,
    "massive": 1.6,
    "huge": 1.6,
    "slightly": 0.6,
    "somewhat": 0.7,
    "modestly": 0.7,
    "marginally": 0.6,
    "reportedly": 0.8,
    "rumored": 0.7,
}

# Base sign and weight live in one lexicon; multi-word terms are token tuples.
LEXICON: dict[tuple[str, ...], float] = {
    # -- bullish ------------------------------------------------------------
    ("bullish",): 1.5,
    ("rally",): 1.5,
    ("rallying",): 1.5,
    ("surge",): 1.5,
    ("surges",): 1.5,
    ("soaring",): 1.5,
    ("soars",): 1.5,
    ("upgrade",): 1.5,
    ("upgraded",): 1.5,
    ("outperform",): 1.2,
    ("beat",): 1.2,
    ("beats",): 1.2,
    ("record",): 1.2,
    ("backlog",): 1.2,
    ("expanding",): 1.0,
    ("expansion",): 1.0,
    ("wins",): 1.2,
    ("win",): 1.2,
    ("landed",): 1.0,
    ("demand",): 0.8,
    ("sold",): 0.8,
    ("out",): 0.5,
    ("shortage",): 1.0,
    ("breakthrough",): 1.5,
    ("upside",): 1.0,
    ("momentum",): 1.0,
    ("accelerating",): 1.2,
    ("strong",): 1.0,
    ("stronger",): 1.0,
    ("beat", "expectations"): 2.0,
    ("record", "backlog"): 2.0,
    ("record", "demand"): 2.0,
    ("sold", "out"): 1.5,
    ("signed", "contract"): 1.5,
    ("wins", "contract"): 2.0,
    ("take-or-pay",): 1.5,
    ("waitlist",): 1.0,
    ("booked",): 1.0,
    ("fully", "booked"): 1.8,
    ("capacity", "constraints"): 1.0,
    ("raising", "guidance"): 2.0,
    ("raised", "guidance"): 2.0,
    ("approvals",): 0.8,
    ("approved",): 0.8,
    ("groundbreaking",): 1.0,
    ("energized",): 1.0,
    ("online",): 0.6,
    ("hiring",): 0.6,
    ("growth",): 0.8,
    ("profitable",): 1.0,
    # -- bearish (negative weights) ----------------------------------------
    ("bearish",): -1.5,
    ("crash",): -1.8,
    ("crashes",): -1.8,
    ("crashing",): -1.8,
    ("plunge",): -1.5,
    ("plunges",): -1.5,
    ("slump",): -1.2,
    ("downgrade",): -1.5,
    ("downgraded",): -1.5,
    ("miss",): -1.2,
    ("misses",): -1.2,
    ("missed",): -1.2,
    ("delay",): -1.0,
    ("delayed",): -1.0,
    ("delays",): -1.0,
    ("delaying",): -1.0,
    ("stalled",): -1.0,
    ("stalls",): -1.0,
    ("paused",): -0.8,
    ("canceled",): -1.2,
    ("cancelled",): -1.2,
    ("withdrawn",): -1.2,
    ("cuts",): -1.0,
    ("cut",): -0.8,
    ("layoffs",): -1.2,
    ("overbuild",): -1.2,
    ("overbuilt",): -1.2,
    ("oversupply",): -1.2,
    ("glut",): -1.2,
    ("bubble",): -1.5,
    ("boondoggle",): -1.5,
    ("money-losing",): -1.2,
    ("underwater",): -1.0,
    ("blackout",): -1.5,
    ("blackouts",): -1.5,
    ("brownouts",): -1.2,
    ("curtailment",): -1.0,
    ("curtailed",): -1.0,
    ("moratorium",): -1.2,
    ("lawsuit",): -0.8,
    ("lawsuits",): -0.8,
    ("protest",): -0.8,
    ("protests",): -0.8,
    ("opposition",): -0.8,
    ("pushback",): -0.8,
    ("scrutiny",): -0.6,
    ("investigation",): -0.8,
    ("bust",): -1.5,
    ("worse-than-expected",): -1.5,
    ("cutting", "guidance"): -2.0,
    ("cut", "guidance"): -2.0,
    ("lowered", "guidance"): -2.0,
    ("strikes",): -0.8,
    ("utility", "bill", "shock"): -1.2,
}


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens; deterministic, locale-independent."""
    return TOKEN_RE.findall(text.lower())


def score_text(text: str) -> float:
    """Sentiment of ``text`` in [-1, +1]; pure — no network, no clock."""
    tokens = tokenize(text)
    total = 0.0
    for i, _ in enumerate(tokens):
        for term, weight in LEXICON.items():
            n = len(term)
            if tuple(tokens[i : i + n]) != term:
                continue
            context = tokens[max(0, i - CONTEXT_WINDOW) : i]
            if any(t in NEGATORS for t in context):
                weight = -weight
            intensities = [INTENSIFIERS[t] for t in context if t in INTENSIFIERS]
            factor = max(intensities) if intensities else 1.0
            total += weight * factor
    # tanh keeps the scale bounded and dampens lexicon-stacking; divisor 5.0
    # means a single 1.5-weight term lands near 0.29, a strong mixed post near ±0.8.
    return round(math.tanh(total / 5.0), 4)


def score_posts(texts: list[str]) -> list[float]:
    """Scores for a batch, in input order — convenience over score_text."""
    return [score_text(text) for text in texts]
