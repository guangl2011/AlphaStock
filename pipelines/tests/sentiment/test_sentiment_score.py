"""Scorer golden fixtures and determinism — no network, no clock, ever."""

import pytest

from pipelines.sentiment.score import score_text

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


def test_intensity_scales_the_magnitude():
    plain = score_text("Data center demand is strong")
    intense = score_text("Data center demand is very strong")
    assert intense > plain


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
        "bullish bullish bullish bullish bullish bullish bullish bullish bullish bullish",
        "crash crash crash crash crash crash crash crash crash crash",
    ]
    for text in extremes:
        assert -1.0 <= score_text(text) <= 1.0
