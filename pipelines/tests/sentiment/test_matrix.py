"""Matrix library v2: schema round-trip, variant expansion, invalid-row rejection."""

import json
from pathlib import Path

import pytest

from pipelines.sentiment.matrix import (
    MatrixError,
    expand_reddit_queries,
    expand_web_queries,
    fill_variant,
    load_matrix,
    parse_matrix,
)

REAL_MATRIX = Path(__file__).resolve().parents[2] / "data" / "sentiment" / "matrix.json"

VARIANTS = [
    '"{keyword}"',
    "{keyword} OR {keyword_or}",
    "{keyword} ({ticker})",
    "thoughts on {keyword}",
]
X_VARIANTS = [
    "{keyword} (site:x.com OR site:twitter.com)",
    "{keyword} OR {keyword_or} (site:x.com OR site:twitter.com)",
    "{keyword} ({ticker}) (site:x.com OR site:twitter.com)",
    "thoughts on {keyword} (site:x.com OR site:twitter.com)",
]


def base_topic() -> dict:
    return {
        "topic_id": "test-topic",
        "label_en": "Test topic",
        "label_zh": "测试主题",
        "keywords": ["kw one", "kw two"],
        "tickers": ["AAA", "BBB"],
        "reddit": {
            "subreddits": {
                "primary": ["testsub", "testsub2", "testsub3"],
                "fallback": ["testfallback", "testfallback2", "testfallback3"],
            },
            "query_variants": VARIANTS,
        },
        "x": {"query_variants": X_VARIANTS},
        "linkedin": {
            "query_variants": [
                v.replace("site:x.com OR site:twitter.com", "site:linkedin.com/posts")
                for v in X_VARIANTS
            ]
        },
        "recency_days": 7,
    }


def raw_matrix(topics: list[dict]) -> dict:
    return {
        "version": "2.0.0",
        "sample_targets": {"fetch_raw": 150, "keep_unique": 100},
        "topics": topics,
    }


def test_real_matrix_loads_and_is_six_topics():
    matrix = load_matrix()
    assert matrix.version
    assert len(matrix.topics) == 6
    assert matrix.topic_ids() == [
        "dc-capex",
        "ai-power-demand",
        "interconnection-backlog",
        "cooling-tech",
        "gpu-supply",
        "reit-leasing",
    ]


def test_real_matrix_carries_sample_targets():
    matrix = load_matrix()
    assert matrix.sample_targets.fetch_raw >= matrix.sample_targets.keep_unique
    assert matrix.sample_targets.keep_unique >= 100


def test_every_topic_has_tiered_subreddits_variants_and_expands_everywhere():
    matrix = load_matrix()
    for topic in matrix.topics:
        tiers = topic.reddit.subreddits
        assert len(tiers.primary) == 3
        assert len(tiers.fallback) == 3
        assert not (set(tiers.primary) & set(tiers.fallback))
        assert 3 <= len(topic.reddit.query_variants) <= 5
        for platform in ("x-indexed", "linkedin-indexed"):
            spec = topic.x if platform == "x-indexed" else topic.linkedin
            assert 3 <= len(spec.query_variants) <= 5
        # subreddit x keyword x variant fan-out
        base = len(tiers.primary) * len(topic.keywords) * len(topic.reddit.query_variants)
        assert len(expand_reddit_queries(topic)) == base
        assert len(expand_reddit_queries(topic, include_fallback=True)) == len(tiers.all()) * len(
            topic.keywords
        ) * len(topic.reddit.query_variants)
        for platform in ("x-indexed", "linkedin-indexed"):
            spec = topic.x if platform == "x-indexed" else topic.linkedin
            expanded = expand_web_queries(topic, platform)
            assert len(expanded) == len(topic.keywords) * len(spec.query_variants)


def test_expansion_snapshot():
    matrix = load_matrix()
    topic = matrix.topic("dc-capex")
    expanded = expand_reddit_queries(topic)
    # First subreddit, first keyword, four variants in order.
    assert expanded[0] == ("datacenter", '"data center capex"')
    assert expanded[1] == ("datacenter", "data center capex OR hyperscaler buildout")
    assert expanded[2] == ("datacenter", "data center capex (APLD)")
    assert expanded[3] == ("datacenter", "thoughts on data center capex")
    # Fallback tier appends its subreddits after the primary ones.
    full = expand_reddit_queries(topic, include_fallback=True)
    assert full[0][0] == "datacenter"
    assert {sub for sub, _ in full} >= {"datacenter", "technology", "investing", "RealEstate"}
    assert expand_web_queries(topic, "x-indexed")[0] == (
        "data center capex (site:x.com OR site:twitter.com)"
    )
    assert expand_web_queries(topic, "linkedin-indexed")[0] == (
        "data center capex (site:linkedin.com/posts)"
    )


def test_relax_phrases_strips_quoting_only():
    matrix = load_matrix()
    topic = matrix.topic("dc-capex")
    relaxed = expand_reddit_queries(topic, relax_phrases=True)
    assert ("datacenter", '"data center capex"') not in relaxed
    assert ("datacenter", "data center capex") in relaxed


def test_fill_variant_cycles_keywords_and_takes_first_ticker():
    keywords = ["alpha", "beta", "gamma"]
    assert fill_variant("{keyword} OR {keyword_or}", "alpha", keywords, ["AAA"]) == "alpha OR beta"
    assert fill_variant("{keyword} OR {keyword_or}", "gamma", keywords, ["AAA"]) == "gamma OR alpha"
    assert fill_variant("{keyword} ({ticker})", "alpha", keywords, ["AAA", "BBB"]) == "alpha (AAA)"


def test_adding_a_topic_is_a_data_edit():
    matrix = parse_matrix(raw_matrix([base_topic()]))
    topic = matrix.topic("test-topic")
    assert topic.label_zh == "测试主题"
    assert topic.reddit.subreddits.primary == ["testsub", "testsub2", "testsub3"]


def test_unknown_topic_id_fails_loudly():
    matrix = load_matrix()
    with pytest.raises(MatrixError, match="unknown topic_id"):
        matrix.topic("no-such-topic")


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda t: t.pop("label_zh"), "label_zh"),
        (lambda t: t.update({"topic_id": "Bad Slug"}), "topic_id"),
        (lambda t: t.update({"recency_days": 0}), "recency_days"),
        (lambda t: t.update({"tickers": ["aaa"]}), "tickers"),
        (lambda t: t["reddit"].pop("query_variants"), "query_variants"),
        (lambda t: t["reddit"].update(
            {"query_variants": ["{a}", "{b}", "{c}", "no placeholder"]}
        ), "placeholder"),
        (lambda t: t["reddit"].update({"query_variants": ['"{k}"', "{keyword}"]}), "3-5"),
        (
            lambda t: t["reddit"].update(
                {"query_variants": VARIANTS + ["extra {keyword}", "extra2 {keyword}"]}
            ),
            "3-5",
        ),
        (lambda t: t["reddit"].pop("subreddits"), "subreddits"),
        (lambda t: t["reddit"].update(
            {"subreddits": {"primary": ["s"], "fallback": []}}
        ), "fallback"),
        (lambda t: t["reddit"].update(
            {"subreddits": {"primary": [], "fallback": ["s"]}}
        ), "primary"),
        (lambda t: t["x"].pop("query_variants"), "query_variants"),
        (lambda t: t.pop("x"), "x"),
        (lambda t: t.update({"keywords": []}), "keywords"),
    ],
)
def test_invalid_rows_are_rejected(mutation, message):
    topic = base_topic()
    mutation(topic)
    with pytest.raises(MatrixError, match=message):
        parse_matrix(raw_matrix([topic]))


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda m: m.pop("sample_targets"), "sample_targets"),
        (lambda m: m.update(
            {"sample_targets": {"fetch_raw": 100, "keep_unique": 150}}
        ), "must be >="),
        (lambda m: m.update({"sample_targets": {"fetch_raw": 0, "keep_unique": 0}}), "positive"),
        (lambda m: m.update({"sample_targets": "150/100"}), "must be an object"),
    ],
)
def test_invalid_sample_targets_are_rejected(mutation, message):
    matrix = raw_matrix([base_topic()])
    mutation(matrix)
    with pytest.raises(MatrixError, match=message):
        parse_matrix(matrix)


def test_duplicate_topic_ids_are_rejected():
    with pytest.raises(MatrixError, match="duplicate topic_ids"):
        parse_matrix(raw_matrix([base_topic(), base_topic()]))


def test_missing_matrix_file_fails_loudly():
    with pytest.raises(MatrixError, match="not found"):
        load_matrix(Path("/nonexistent/matrix.json"))


def test_real_matrix_is_valid_json_at_the_declared_version():
    raw = json.loads(REAL_MATRIX.read_text(encoding="utf-8"))
    matrix = parse_matrix(raw, REAL_MATRIX)
    assert matrix.version == "2.0.0"
