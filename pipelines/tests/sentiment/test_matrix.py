"""Matrix library: schema round-trip, template expansion, invalid-row rejection."""

import json
from pathlib import Path

import pytest

from pipelines.sentiment.matrix import (
    MatrixError,
    expand_reddit_queries,
    expand_web_queries,
    load_matrix,
    parse_matrix,
)

REAL_MATRIX = Path(__file__).resolve().parents[2] / "data" / "sentiment" / "matrix.json"


def base_topic() -> dict:
    return {
        "topic_id": "test-topic",
        "label_en": "Test topic",
        "label_zh": "测试主题",
        "keywords": ["kw one", "kw two"],
        "tickers": ["AAA", "BBB"],
        "reddit": {"subreddits": ["testsub"], "query_template": '"{keyword}"'},
        "x": {"query_template": "{keyword} (site:x.com OR site:twitter.com)"},
        "linkedin": {"query_template": "{keyword} site:linkedin.com/posts"},
        "recency_days": 7,
    }


def raw_matrix(topics: list[dict]) -> dict:
    return {"version": "1.0.0", "topics": topics}


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


def test_every_topic_has_bilingual_labels_and_expands_on_all_platforms():
    matrix = load_matrix()
    for topic in matrix.topics:
        assert topic.label_en and topic.label_zh
        assert topic.tickers
        queries = expand_reddit_queries(topic)
        assert len(queries) == len(topic.reddit.subreddits) * len(topic.keywords)
        for platform in ("x-indexed", "linkedin-indexed"):
            expanded = expand_web_queries(topic, platform)
            assert len(expanded) == len(topic.keywords)
            for query in expanded:
                assert "{keyword}" not in query


def test_expansion_snapshot():
    matrix = load_matrix()
    topic = matrix.topic("dc-capex")
    assert expand_reddit_queries(topic)[:3] == [
        ("datacenter", '"data center capex"'),
        ("datacenter", '"hyperscaler buildout"'),
        ("datacenter", '"AI infrastructure spending"'),
    ]
    assert expand_web_queries(topic, "x-indexed")[0] == (
        "data center capex (site:x.com OR site:twitter.com)"
    )
    assert expand_web_queries(topic, "linkedin-indexed")[0] == (
        "data center capex site:linkedin.com/posts"
    )


def test_adding_a_topic_is_a_data_edit():
    matrix = parse_matrix(raw_matrix([base_topic()]))
    topic = matrix.topic("test-topic")
    assert topic.label_zh == "测试主题"
    assert topic.reddit.subreddits == ["testsub"]


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
        (lambda t: t["reddit"].update({"query_template": "no placeholder"}), "placeholder"),
        (lambda t: t["reddit"].update({"subreddits": []}), "subreddits"),
        (lambda t: t.pop("x"), "x"),
        (lambda t: t.update({"keywords": []}), "keywords"),
    ],
)
def test_invalid_rows_are_rejected(mutation, message):
    topic = base_topic()
    mutation(topic)
    with pytest.raises(MatrixError, match=message):
        parse_matrix(raw_matrix([topic]))


def test_duplicate_topic_ids_are_rejected():
    with pytest.raises(MatrixError, match="duplicate topic_ids"):
        parse_matrix(raw_matrix([base_topic(), base_topic()]))


def test_missing_matrix_file_fails_loudly():
    with pytest.raises(MatrixError, match="not found"):
        load_matrix(Path("/nonexistent/matrix.json"))


def test_matrix_json_is_valid_json_on_disk():
    parsed = json.loads(REAL_MATRIX.read_text(encoding="utf-8"))
    assert parsed["version"] == "1.0.0"
