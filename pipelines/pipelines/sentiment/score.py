"""Deterministic finance-tuned lexicon sentiment scorer (v2).

Constraints from the blueprint's scoring decision:

* No LLM in the scoring path — a curated lexicon only, so every scan is
  reproducible and auditable.
* No network and no clock: ``score_text`` is a pure function of its input.
  The same text always yields the same score, on any machine, at any time.
* Weights, negators, intensity modifiers, and every calibration constant
  live in a versioned data file (``pipelines/data/sentiment/lexicon.json``);
  this module validates it eagerly at load and owns none of the numbers.

Scoring walks the token list once with longest-match-wins (a 3-word term
shadows the 1-word terms inside it, and consumed tokens are skipped so a
phrase is never double-counted with its own parts). Each match's weight is
adjusted by its local context: intensity modifiers within the three tokens
before the match scale it, and a negator in that window flips its sign. The
total is squashed through tanh into [-1, +1].

v2 additions beyond per-text scoring, all pure:

* ``post_confidence`` — per-post confidence = lexicon coverage x |score
  margin|, squashed to 0-1 and banded low/medium/high.
* ``winsorize`` and ``topic_mean`` — the topic score is a recency-decayed,
  engagement-weighted mean of 5/95-winsorized post scores, so one viral post
  cannot move a topic alone and stale posts fade.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

LEXICON_PATH = Path(__file__).resolve().parents[2] / "data" / "sentiment" / "lexicon.json"

TOKEN_RE = re.compile(r"[a-z']+")

# Context window (tokens before a match) checked for negators and intensifiers.
CONTEXT_WINDOW = 3

MIN_WEIGHT = -3.0
MAX_WEIGHT = 3.0


class LexiconError(ValueError):
    """Raised when the lexicon data file is missing, malformed, or out of bounds."""


@dataclass(frozen=True)
class LexiconModel:
    """Validated snapshot of lexicon.json — the scorer's only source of numbers."""

    version: str
    terms: dict[tuple[str, ...], float]
    max_term_len: int
    negators: frozenset[str]
    intensifiers: dict[str, float]
    tanh_scale: float
    confidence_saturation_matches: float
    confidence_band_high: float
    confidence_band_medium: float
    recency_half_life_days: float


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise LexiconError(message)


def load_lexicon(path: Path | str | None = None) -> LexiconModel:
    """Parse and validate lexicon.json; a bad entry fails loudly, never silently."""
    lexicon_path = Path(path) if path else LEXICON_PATH
    if not lexicon_path.is_file():
        raise LexiconError(f"lexicon file not found: {lexicon_path}")
    try:
        raw = json.loads(lexicon_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise LexiconError(f"lexicon file is malformed: {error}") from error

    _require(isinstance(raw, dict), "lexicon must be an object")
    terms_raw = raw.get("terms")
    _require(
        isinstance(terms_raw, list) and len(terms_raw) >= 150,
        "lexicon.terms must list >= 150 entries",
    )
    terms: dict[tuple[str, ...], float] = {}
    for entry in terms_raw:
        _require(isinstance(entry, dict), "each lexicon term must be an object")
        term, weight = entry.get("term"), entry.get("weight")
        _require(
            isinstance(term, str) and term.strip() and term == term.lower(),
            f"lexicon term {term!r} must be a lowercase non-empty string",
        )
        _require(
            isinstance(weight, (int, float)) and not isinstance(weight, bool)
            and MIN_WEIGHT <= weight <= MAX_WEIGHT,
            f"lexicon term {term!r} weight must be a number in [{MIN_WEIGHT}, {MAX_WEIGHT}]",
        )
        tokens = tuple(TOKEN_RE.findall(term))
        _require(len(tokens) > 0, f"lexicon term {term!r} must contain word tokens")
        _require(tokens not in terms, f"lexicon has duplicate term {term!r}")
        terms[tokens] = float(weight)

    negators_raw = raw.get("negators")
    _require(
        isinstance(negators_raw, list) and all(isinstance(n, str) and n for n in negators_raw),
        "lexicon.negators must be a list of non-empty strings",
    )
    intensifiers_raw = raw.get("intensifiers")
    _require(
        isinstance(intensifiers_raw, dict),
        "lexicon.intensifiers must be an object",
    )
    intensifiers: dict[str, float] = {}
    for key, value in intensifiers_raw.items():
        _require(
            isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value,
            f"intensifier {key!r} must be a positive number",
        )
        intensifiers[key] = float(value)

    def _positive(key: str) -> float:
        value = raw.get(key)
        _require(
            isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0,
            f"lexicon.{key} must be a positive number",
        )
        return float(value)

    def _band(key: str) -> float:
        value = raw.get(key)
        _require(
            isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1,
            f"lexicon.{key} must be a number in [0, 1]",
        )
        return float(value)

    version = raw.get("version")
    _require(
        isinstance(version, str) and version.strip(), "lexicon.version must be a non-empty string"
    )
    return LexiconModel(
        version=version,
        terms=terms,
        max_term_len=max(len(term) for term in terms),
        negators=frozenset(negators_raw),
        intensifiers=intensifiers,
        tanh_scale=_positive("tanh_scale"),
        confidence_saturation_matches=_positive("confidence_saturation_matches"),
        confidence_band_high=_band("confidence_band_high"),
        confidence_band_medium=_band("confidence_band_medium"),
        recency_half_life_days=_positive("recency_half_life_days"),
    )


# Loaded once at import; the data file ships with the package and never changes
# at runtime, so module-level state stays deterministic.
LEXICON = load_lexicon()


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens; deterministic, locale-independent."""
    return TOKEN_RE.findall(text.lower())


@dataclass(frozen=True)
class _Match:
    """One lexicon hit after context adjustment."""

    length: int
    effective_weight: float


def _match_terms(tokens: list[str]) -> list[_Match]:
    """Longest-match-wins lexicon walk; consumed tokens are skipped."""
    matches: list[_Match] = []
    i = 0
    while i < len(tokens):
        matched = None
        for n in range(min(LEXICON.max_term_len, len(tokens) - i), 0, -1):
            candidate = tuple(tokens[i : i + n])
            if candidate in LEXICON.terms:
                matched = (n, LEXICON.terms[candidate])
                break
        if matched is None:
            i += 1
            continue
        n, weight = matched
        context = tokens[max(0, i - CONTEXT_WINDOW) : i]
        if any(t in LEXICON.negators for t in context):
            weight = -weight
        intensities = [LEXICON.intensifiers[t] for t in context if t in LEXICON.intensifiers]
        factor = max(intensities) if intensities else 1.0
        matches.append(_Match(length=n, effective_weight=weight * factor))
        i += n
    return matches


def score_text(text: str) -> float:
    """Sentiment of ``text`` in [-1, +1]; pure — no network, no clock."""
    total = sum(match.effective_weight for match in _match_terms(tokenize(text)))
    # tanh keeps the scale bounded and dampens lexicon-stacking; the scale
    # constant lives in lexicon.json so a single 1.5-weight term lands near
    # 0.29 and a strong mixed post near ±0.8.
    return round(math.tanh(total / LEXICON.tanh_scale), 4)


def score_posts(texts: list[str]) -> list[float]:
    """Scores for a batch, in input order — convenience over score_text."""
    return [score_text(text) for text in texts]


def lexicon_coverage(text: str) -> float:
    """Share of the confidence saturation reached by this text's lexicon hits."""
    matches = _match_terms(tokenize(text))
    return min(1.0, len(matches) / LEXICON.confidence_saturation_matches)


def post_confidence(text: str) -> float:
    """Per-post confidence in [0, 1]: coverage x |score margin|."""
    return round(min(1.0, lexicon_coverage(text) * abs(score_text(text))), 4)


def post_confidence_band(confidence: float) -> str:
    """low / medium / high band for a per-post confidence in [0, 1]."""
    if confidence >= LEXICON.confidence_band_high:
        return "high"
    if confidence >= LEXICON.confidence_band_medium:
        return "medium"
    return "low"


def winsorize(scores: list[float], lower: float = 0.05, upper: float = 0.95) -> list[float]:
    """5/95 winsorization: extreme values are pulled to the percentile, not dropped."""
    if not scores:
        return []
    ordered = sorted(scores)

    def percentile(q: float) -> float:
        rank = max(1, math.ceil(q * len(ordered)))
        return ordered[min(rank, len(ordered)) - 1]

    low, high = percentile(lower), percentile(upper)
    return [min(max(score, low), high) for score in scores]


@dataclass(frozen=True)
class AggregatePost:
    """One scored post with the signals the topic mean weights by."""

    score: float
    engagement: float  # reddit: score + comments; indexed: position proxy in (0, 1]
    engagement_is_proxy: bool
    post_date: str | None  # ISO date; None means no decay is applied


def engagement_weight(post: AggregatePost) -> float:
    """log-scaled engagement floor: zero engagement still weighs 1.0.

    Indexed posts carry a weak position proxy instead of true engagement, so
    their spread is naturally small — the proxy's honesty lives in the flag,
    not in a different formula.
    """
    return 1.0 + math.log1p(max(0.0, post.engagement))


def recency_decay(post: AggregatePost, as_of: date) -> float:
    """Exponential decay at the lexicon's half-life; unknown dates do not decay."""
    if post.post_date is None:
        return 1.0
    try:
        age_days = max(0, (as_of - date.fromisoformat(post.post_date)).days)
    except ValueError:
        return 1.0
    return 0.5 ** (age_days / LEXICON.recency_half_life_days)


def topic_mean(posts: list[AggregatePost], as_of: date) -> float:
    """Recency-decayed, engagement-weighted mean of winsorized scores in [-1, 1]."""
    if not posts:
        return 0.0
    scores = winsorize([post.score for post in posts])
    weights = [engagement_weight(post) * recency_decay(post, as_of) for post in posts]
    total_weight = sum(weights)
    if total_weight <= 0:  # unreachable (weights are strictly positive) but honest
        return 0.0
    return round(
        sum(s * w for s, w in zip(scores, weights, strict=False)) / total_weight, 4
    )
