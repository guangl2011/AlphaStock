"""Scan pipeline output: schema, empty topics, append-only staging."""

import json
from datetime import date
from pathlib import Path

import pytest

from pipelines.sentiment.fetchers import FetchedPost
from pipelines.sentiment.matrix import load_matrix
from pipelines.sentiment.run_scan import ScanOptions, parse_as_of, run_scan, topic_confidence

AS_OF = "2026-11-18T12:00:00Z"


class FakeReddit:
    """Stands in for RedditFetcher: deterministic posts, no network."""

    def __init__(self, posts_by_topic: dict[str, list[FetchedPost]]):
        self.posts_by_topic = posts_by_topic
        self.requested: list[tuple[str, list, date]] = []

    def fetch(self, topic_id, queries, window_end):  # noqa: ANN001
        self.requested.append((topic_id, queries, window_end))
        return self.posts_by_topic.get(topic_id, [])


def post(platform: str, url: str, author: str = "u1") -> FetchedPost:
    return FetchedPost(
        platform=platform,
        author=author,
        url=url,
        post_date="2026-11-17",
        snippet="Hyperscalers keep beating expectations — record backlog.",
    )


def options(tmp_path: Path, topics: tuple[str, ...] | None = None) -> ScanOptions:
    return ScanOptions(
        platforms=("reddit",),
        topic_ids=topics,
        as_of=AS_OF,
        cache_dir=tmp_path / "cache",
        out_dir=tmp_path / "out",
    )


def test_run_stages_schema_complete_rows(tmp_path: Path):
    fake = FakeReddit(
        {
            "dc-capex": [
                post("reddit", "https://www.reddit.com/r/datacenter/comments/a/"),
                post("reddit", "https://www.reddit.com/r/stocks/comments/b/", author="u2"),
            ]
        }
    )
    run_path, payload = run_scan(load_matrix(), options(tmp_path), reddit=fake)

    block = next(b for b in payload["topics"] if b["topic_id"] == "dc-capex")
    assert block["post_count"] == 2
    assert block["topic_score"] == pytest.approx(
        sum(block["posts"][i]["raw_score"] for i in range(2)) / 2
    )
    assert 0.0 <= block["confidence"] <= 1.0
    assert block["platform_counts"] == {"reddit": 2, "x-indexed": 0, "linkedin-indexed": 0}
    for row in block["posts"]:
        required = {
            "topic_id",
            "platform",
            "author",
            "url",
            "post_date",
            "snippet",
            "raw_score",
            "topic_score",
            "confidence",
            "as_of",
        }
        assert required <= set(row)
        assert row["as_of"] == AS_OF
        assert -1.0 <= row["raw_score"] <= 1.0
    assert run_path.is_file()
    assert json.loads(run_path.read_text(encoding="utf-8"))["run_id"] == AS_OF


def test_zero_result_topic_is_recorded_not_dropped(tmp_path: Path):
    fake = FakeReddit({})  # nothing found anywhere
    _, payload = run_scan(load_matrix(), options(tmp_path), reddit=fake)

    assert len(payload["topics"]) == 6
    empty = next(b for b in payload["topics"] if b["topic_id"] == "reit-leasing")
    assert empty["posts"] == []
    assert empty["post_count"] == 0
    assert empty["topic_score"] == 0.0
    assert empty["confidence"] == 0.0


def test_selected_topics_only(tmp_path: Path):
    fake = FakeReddit({})
    _, payload = run_scan(
        load_matrix(), options(tmp_path, topics=("dc-capex", "cooling-tech")), reddit=fake
    )
    assert [b["topic_id"] for b in payload["topics"]] == ["dc-capex", "cooling-tech"]


def test_output_is_append_only(tmp_path: Path):
    fake = FakeReddit({})
    matrix = load_matrix()
    run_scan(matrix, options(tmp_path), reddit=fake)
    with pytest.raises(FileExistsError, match="append-only"):
        run_scan(matrix, options(tmp_path), reddit=fake)


def test_latest_summary_written(tmp_path: Path):
    fake = FakeReddit({})
    run_scan(load_matrix(), options(tmp_path), reddit=fake)
    latest = tmp_path / "out" / "latest.json"
    assert latest.is_file()
    assert len(json.loads(latest.read_text(encoding="utf-8"))["topics"]) == 6


def test_unknown_platform_rejected(tmp_path: Path):
    bad = ScanOptions(
        platforms=("twitter",),
        topic_ids=None,
        as_of=AS_OF,
        cache_dir=tmp_path / "cache",
        out_dir=tmp_path / "out",
    )
    with pytest.raises(ValueError, match="unknown platforms"):
        run_scan(load_matrix(), bad, reddit=FakeReddit({}))


def test_topic_confidence_saturates_and_respects_spread():
    assert topic_confidence(0, 0) == 0.0
    assert topic_confidence(15, 3) == 1.0
    # One author caps the spread component.
    assert topic_confidence(15, 1) == pytest.approx(0.5 + 0.5 / 3, abs=0.01)


def test_parse_as_of_normalizes_to_utc():
    assert parse_as_of("2026-11-18T12:00:00Z") == "2026-11-18T12:00:00Z"
    assert parse_as_of("2026-11-18T12:00:00") == "2026-11-18T12:00:00Z"
    with pytest.raises(ValueError, match="ISO-8601"):
        parse_as_of("not-a-date")
