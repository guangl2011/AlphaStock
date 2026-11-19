"""Scan pipeline output: schema, empty topics, append-only staging."""

import json
from datetime import date
from pathlib import Path

import pytest

from pipelines.sentiment.fetchers import FetchedPost, FetchError
from pipelines.sentiment.matrix import load_matrix
from pipelines.sentiment.run_scan import (
    ScanOptions,
    parse_as_of,
    run_scan,
    topic_confidence,
)

AS_OF = "2026-11-18T12:00:00Z"


class FakeReddit:
    """Stands in for RedditTregFetcher: deterministic posts, no network."""

    def __init__(self, posts_by_topic: dict[str, list[FetchedPost]]):
        self.posts_by_topic = posts_by_topic
        self.requested: list[tuple[str, list, date, str]] = []

    def fetch(self, topic_id, queries, window_end, *, t="week"):  # noqa: ANN001
        self.requested.append((topic_id, queries, window_end, t))
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


class CountingReddit:
    """Returns n fresh unique posts per fetch call, regardless of the query."""

    def __init__(self, posts_per_call: int, repeat_urls: bool = False):
        self.posts_per_call = posts_per_call
        self.repeat_urls = repeat_urls
        self.calls = 0
        self.requested: list[tuple[str, list, date, str]] = []

    def fetch(self, topic_id, queries, window_end, *, t="week"):  # noqa: ANN001
        self.calls += 1
        self.requested.append((topic_id, queries, window_end, t))
        base = 0 if self.repeat_urls else self.calls
        return [
            FetchedPost(
                platform="reddit",
                author=f"u{base}-{i}",
                url=f"https://www.reddit.com/r/datacenter/comments/{base}/{i}/",
                post_date="2026-11-17",
                snippet=f"Hyperscaler capex post {base}-{i} — record backlog.",
            )
            for i in range(self.posts_per_call)
        ]


def test_coverage_block_reports_fetched_deduped_kept_and_ladder(tmp_path: Path):
    run_path, payload = run_scan(load_matrix(), options(tmp_path), reddit=CountingReddit(25))

    coverage = payload["coverage"]["reddit"]
    assert coverage["keep_target"] == 100
    assert coverage["kept"] == len(
        [p for b in payload["topics"] for p in b["posts"] if p["platform"] == "reddit"]
    )
    assert coverage["deduped"] == coverage["kept"]  # no duplicates in this fixture
    assert coverage["fetched"] >= coverage["deduped"]
    assert coverage["topics"]["dc-capex"]["kept"] == 25
    assert run_path.is_file() and payload["sample_targets"]["keep_unique"] == 100


def test_ladder_escalates_until_the_keep_target_is_met(tmp_path: Path):
    # 6 topics x 15 posts per call: L0 keeps 90 (<120 -> L1), L1 reaches 180.
    _, payload = run_scan(load_matrix(), options(tmp_path), reddit=CountingReddit(15))

    coverage = payload["coverage"]["reddit"]
    assert coverage["ladder_steps"] == ["L0", "L1"]
    assert coverage["kept"] == 180
    assert coverage["effective_window_days"] == 7


def test_ladder_widens_the_window_and_relaxes_phrases_when_thin(tmp_path: Path):
    # 6 topics x 3 posts per call: every rung runs and the floor is still short.
    _, payload = run_scan(load_matrix(), options(tmp_path), reddit=CountingReddit(3))

    coverage = payload["coverage"]["reddit"]
    assert coverage["ladder_steps"] == ["L0", "L1", "L2a", "L2b", "L3"]
    assert coverage["effective_window_days"] == 30
    assert coverage["kept"] < coverage["keep_target"]  # honest shortfall, never padded


def test_ladder_escalation_stops_at_the_keep_target(tmp_path: Path):
    # 6 topics x 30 posts per call: L0 alone keeps 180 >= 120 — no escalation at all.
    fake = CountingReddit(30)
    _, payload = run_scan(load_matrix(), options(tmp_path), reddit=fake)

    assert payload["coverage"]["reddit"]["ladder_steps"] == ["L0"]
    assert fake.calls == 6  # one fetch per topic, no wasted rungs


def test_duplicates_never_pad_the_kept_count(tmp_path: Path):
    # Every rung returns the same five posts: dedupe keeps exactly five overall.
    fake = CountingReddit(5, repeat_urls=True)
    _, payload = run_scan(load_matrix(), options(tmp_path), reddit=fake)

    coverage = payload["coverage"]["reddit"]
    assert coverage["kept"] == 5
    assert coverage["fetched"] > coverage["deduped"]


def test_empty_topic_stays_empty_under_the_controller(tmp_path: Path):
    fake = FakeReddit({"dc-capex": [post("reddit", "https://www.reddit.com/r/datacenter/comments/a/")]})
    _, payload = run_scan(load_matrix(), options(tmp_path), reddit=fake)

    empty = next(b for b in payload["topics"] if b["topic_id"] == "reit-leasing")
    assert empty["posts"] == []
    assert empty["post_count"] == 0
    assert payload["coverage"]["reddit"]["topics"]["reit-leasing"]["kept"] == 0


def test_reddit_window_tier_follows_the_ladder(tmp_path: Path):
    fake = CountingReddit(3)
    run_scan(load_matrix(), options(tmp_path), reddit=fake)
    tiers = [t for _, _, _, t in fake.requested]
    assert "week" in tiers and "month" in tiers


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


# --- lazy fetcher construction -----------------------------------------------


class RecordingTreg:
    """Stands in for TregFetcher; records fetch calls, returns no posts."""

    def __init__(self):
        self.calls = 0

    def fetch(self, topic_id, platform, queries, window_end, *, window_days=7):  # noqa: ANN001
        self.calls += 1
        return []


def treg_only_options(tmp_path: Path) -> ScanOptions:
    return ScanOptions(
        platforms=("x-indexed", "linkedin-indexed"),
        topic_ids=None,
        as_of=AS_OF,
        cache_dir=tmp_path / "cache",
        out_dir=tmp_path / "out",
    )


def test_treg_only_run_never_constructs_reddit_fetcher(tmp_path: Path, monkeypatch):
    def boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("RedditTregFetcher must not be built for a treg-only run")

    monkeypatch.setattr("pipelines.sentiment.run_scan.RedditTregFetcher", boom)
    treg = RecordingTreg()
    run_path, payload = run_scan(load_matrix(), treg_only_options(tmp_path), treg=treg)

    assert run_path.is_file()
    assert treg.calls > 0  # the treg fetchers ran, and only them
    assert payload["platforms"] == ["x-indexed", "linkedin-indexed"]


def test_treg_fetcher_constructed_lazily_when_not_injected(tmp_path: Path, monkeypatch):
    built: list[Path] = []

    def factory(cache_dir):
        built.append(cache_dir)
        return RecordingTreg()

    monkeypatch.setattr("pipelines.sentiment.run_scan.TregFetcher", factory)
    run_path, _ = run_scan(load_matrix(), treg_only_options(tmp_path))  # no treg injected

    assert built == [tmp_path / "cache" / "treg"]  # constructed once, on first use
    assert run_path.is_file()


def test_reddit_run_without_treg_token_still_raises_loud_actionable_error(
    tmp_path: Path, monkeypatch
):
    for var in ("TREG_TOKEN", "TREG_MAX_COST"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(FetchError, match="TREG_TOKEN"):
        run_scan(load_matrix(), options(tmp_path))  # platforms=("reddit",), nothing injected
