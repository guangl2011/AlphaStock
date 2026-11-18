"""Scorer v2 golden fixtures and determinism — no network, no clock, ever."""

import json
from datetime import date
from pathlib import Path

import pytest

from pipelines.sentiment.score import (
    LEXICON,
    LEXICON_PATH,
    AggregatePost,
    LexiconError,
    engagement_weight,
    lexicon_coverage,
    load_lexicon,
    post_confidence,
    post_confidence_band,
    recency_decay,
    score_text,
    tokenize,
    topic_mean,
    winsorize,
)

AS_OF = date(2026, 11, 18)


def ap(
    score: float, engagement: float = 0.0, post_date: str | None = None, proxy: bool = False
) -> AggregatePost:
    """AggregatePost with the common defaults, so weight tests read on one line."""
    return AggregatePost(
        score=score, engagement=engagement, engagement_is_proxy=proxy, post_date=post_date
    )

GOLDEN = [
    # (text, expectation)
    ("Data center demand is very strong, hyperscalers keep beating expectations.", "positive"),
    ("Huge new contract win — record backlog and fully booked capacity.", "positive"),
    ("Data center capex is crashing and the builders are cutting guidance.", "negative"),
    ("Grid operators warn of blackouts as demand outpaces supply.", "negative"),
    ("The utility commission meeting is scheduled for Tuesday.", "neutral"),
    ("", "neutral"),
]


@pytest.mark.parametrize("text, expectation", GOLDEN)
def test_golden_scores(text, expectation):
    score = score_text(text)
    assert -1.0 <= score <= 1.0
    if expectation == "positive":
        assert score > 0.05, text
    elif expectation == "negative":
        assert score < -0.05, text
    else:
        assert score == 0.0, text


def test_negation_flips_the_sign():
    bullish = score_text("I am bullish on data center REITs")
    negated = score_text("I am not bullish on data center REITs")
    assert bullish > 0
    assert negated < 0
    assert negated == -bullish


def test_negation_scope_is_the_three_token_window():
    # "no" sits directly before the bearish term: the weight flips to positive.
    bare = score_text("delay risk here for the builders")
    negated = score_text("no delay risk here for the builders")
    assert bare < 0
    assert negated == -bare
    # "no" sits 4 tokens before "delay": outside the window, weight stands.
    outside = score_text("there was no reason to expect the project would slip a delay")
    plain = score_text("there was reason to expect the project would slip a delay")
    assert outside == plain < 0


def test_intensity_scales_the_magnitude():
    plain = score_text("Data center demand is strong")
    intense = score_text("Data center demand is very strong")
    softer = score_text("Data center demand is slightly strong")
    assert intense > plain > softer > 0


def test_intensity_downweight_bearish_too():
    plain = score_text("the project is delayed")
    softer = score_text("the project is slightly delayed")
    assert plain < softer < 0


def test_longest_match_wins_no_double_counting():
    # "record backlog" is one 2.0 term — its parts must not also score.
    # Coverage counts matches: one occurrence is exactly one match, however
    # many single-word terms it contains.
    assert lexicon_coverage("record backlog") == pytest.approx(
        1 / LEXICON.confidence_saturation_matches
    )
    assert lexicon_coverage("record backlog record backlog") == pytest.approx(
        2 / LEXICON.confidence_saturation_matches
    )
    # The phrase outweighs its single-word part (phrase weight 2.0 vs 1.2).
    assert score_text("record backlog") > score_text("backlog") > 0


def test_mixed_text_lands_near_neutral():
    score = score_text("Record backlog, but blackout risk and rising pushback from regulators.")
    assert abs(score) < 0.5


def test_determinism_same_input_same_output():
    text = "Hyperscalers beat expectations; interconnection delays stall new projects."
    runs = {score_text(text) for _ in range(10)}
    assert len(runs) == 1


def test_bilingual_post_scores_by_lexicon_only():
    # Chinese tokens are outside the lexicon: the English terms still score,
    # the Chinese prose contributes nothing — and never crashes.
    assert score_text("数据中心资本开支 strong momentum") > 0
    assert score_text("数据中心资本开支") == 0.0


def test_scores_are_bounded():
    extremes = [
        "bullish " * 30,
        "crash " * 30,
    ]
    for text in extremes:
        assert -1.0 <= score_text(text) <= 1.0


# --- per-post confidence ---------------------------------------------------


def test_post_confidence_rises_with_coverage_and_margin():
    bare = post_confidence("backlog")
    richer = post_confidence("Record backlog, fully booked, beating expectations, strong demand.")
    assert 0.0 < bare < richer <= 1.0


def test_post_confidence_zero_without_lexicon_hits():
    assert post_confidence("The utility commission meeting is scheduled for Tuesday.") == 0.0


def test_post_confidence_bands_are_ordered_and_bounded():
    bands = {
        post_confidence_band(0.0),
        post_confidence_band(0.2),
        post_confidence_band(0.9),
    }
    assert bands == {"low", "medium", "high"}
    assert post_confidence_band(LEXICON.confidence_band_high) == "high"
    assert post_confidence_band(LEXICON.confidence_band_medium) == "medium"


# --- winsorization ---------------------------------------------------------


def test_winsorize_pulls_extremes_to_the_percentiles():
    scores = [0.1] * 18 + [0.2] + [1.0]  # 20 values; 1.0 sits above the 95th
    winsorized = winsorize(scores)
    # Every position keeps its slot; the extreme is pulled to the percentile.
    assert winsorized[:18] == scores[:18]
    assert winsorized[-1] < 1.0


def test_winsorize_on_the_viral_post_case():
    # Ten calm posts and one viral 1.0 post: the mean must stay near the calm level.
    posts = [ap(s) for s in [0.2] * 10 + [1.0]]
    assert topic_mean(posts, AS_OF) < 0.3


def test_winsorize_single_and_empty():
    assert winsorize([]) == []
    single = winsorize([0.42])
    assert single == [0.42]


# --- engagement and recency weighting --------------------------------------


def test_engagement_weight_log_scale():
    assert engagement_weight(ap(0.3)) == 1.0
    assert engagement_weight(ap(0.3, engagement=99.0)) > 1.0


def test_recency_decay_halves_at_the_half_life():
    assert recency_decay(ap(0.3, post_date="2026-11-11"), AS_OF) == pytest.approx(0.5)
    assert recency_decay(ap(0.3, post_date="2026-11-18"), AS_OF) == 1.0

def test_recency_decay_unknown_date_does_not_decay():
    assert recency_decay(ap(0.3), AS_OF) == 1.0


def test_topic_mean_weighting_changes_the_result():
    fresh = ap(0.5, post_date="2026-11-18")
    stale = ap(-0.4, post_date="2026-11-04")
    # Same scores, no decay (both effective weight 1.0): plain average.
    undecayed = ap(-0.4)
    assert topic_mean([fresh, stale], AS_OF) > topic_mean([fresh, undecayed], AS_OF)
    # Recency decay pulls the mean toward the fresh post's score.
    assert topic_mean([fresh, stale], AS_OF) > 0.0


def test_topic_mean_is_deterministic():
    posts = [ap(0.2, engagement=3.0), ap(-0.1, engagement=12.0), ap(0.5), ap(0.9, engagement=1.0)]
    assert topic_mean(posts, AS_OF) == topic_mean(list(reversed(posts)), AS_OF)


# --- lexicon data file validation ------------------------------------------


def test_real_lexicon_loads_with_contract_size():
    assert LEXICON_PATH.is_file()
    assert len(LEXICON.terms) >= 150
    assert LEXICON.max_term_len >= 2  # multi-word phrases are present
    assert LEXICON.tanh_scale > 0


def test_lexicon_terms_are_lowercase_and_within_bounds():
    for term, weight in LEXICON.terms.items():
        joined = " ".join(term)
        assert joined == joined.lower()
        assert -3.0 <= weight <= 3.0
        assert weight != 0.0


def test_missing_lexicon_file_fails_loudly(tmp_path: Path):
    with pytest.raises(LexiconError, match="not found"):
        load_lexicon(tmp_path / "absent.json")


def test_malformed_lexicon_rejected(tmp_path: Path):
    # 150 distinct terms so the size contract passes and the weight check fires.
    terms = [{"term": chr(97 + i // 26) + chr(97 + i % 26), "weight": 1.0} for i in range(150)]
    terms[7]["weight"] = 99.0
    bad = {
        "version": "2.0.0",
        "tanh_scale": 5.0,
        "confidence_saturation_matches": 4.0,
        "confidence_band_high": 0.35,
        "confidence_band_medium": 0.12,
        "recency_half_life_days": 7.0,
        "negators": ["not"],
        "intensifiers": {"very": 1.5},
        "terms": terms,
    }
    path = tmp_path / "lexicon.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(LexiconError, match="weight"):
        load_lexicon(path)


def test_lexicon_below_contract_size_rejected(tmp_path: Path):
    small = {
        "version": "2.0.0",
        "tanh_scale": 5.0,
        "confidence_saturation_matches": 4.0,
        "confidence_band_high": 0.35,
        "confidence_band_medium": 0.12,
        "recency_half_life_days": 7.0,
        "negators": [],
        "intensifiers": {},
        "terms": [{"term": "bullish", "weight": 1.5}],
    }
    path = tmp_path / "lexicon.json"
    path.write_text(json.dumps(small), encoding="utf-8")
    with pytest.raises(LexiconError, match="150"):
        load_lexicon(path)


def test_tokenize_is_deterministic_and_ascii_wordish():
    assert tokenize("Backlog's up; capex!") == ["backlog's", "up", "capex"]
