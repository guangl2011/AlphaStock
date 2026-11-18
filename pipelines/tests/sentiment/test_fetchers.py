"""Fetchers against recorded responses — no test touches the network."""

import json
from datetime import date
from pathlib import Path

import pytest

from pipelines.sentiment.fetchers import (
    FetchError,
    RedditFetcher,
    YouComFetcher,
)

WINDOW_END = date(2026, 11, 18)


class FakeTransport:
    """Recorded responses keyed by URL substring; records every call."""

    def __init__(self, responses: dict[str, tuple[int, str]]):
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str, headers: dict[str, str]) -> tuple[int, str]:
        self.calls.append(url)
        for fragment, response in self.responses.items():
            if fragment in url:
                return response
        return 200, "{}"


def reddit_payload(permalinks: list[str]) -> str:
    return json.dumps(
        {
            "data": {
                "children": [
                    {
                        "data": {
                            "permalink": link,
                            "title": "Hyperscaler capex guides up again",
                            "selftext": "Record backlog and fully booked capacity.",
                            "author": "gridnerd_88",
                            "created_utc": 1769904000,
                        }
                    }
                    for link in permalinks
                ]
            }
        }
    )


def ydc_payload(items: list[dict]) -> str:
    return json.dumps({"results": {"web": items}})


def test_reddit_recorded_response_parses_posts(tmp_path: Path):
    transport = FakeTransport(
        {"/r/datacenter/": (200, reddit_payload(["/r/datacenter/comments/abc/hyperscaler_capex/"]))}
    )
    fetcher = RedditFetcher(tmp_path, transport=transport, sleep=lambda _s: None)
    posts = fetcher.fetch("dc-capex", [("datacenter", '"data center capex"')], WINDOW_END)

    assert len(posts) == 1
    post = posts[0]
    assert post.platform == "reddit"
    assert post.author == "gridnerd_88"
    assert post.url == "https://www.reddit.com/r/datacenter/comments/abc/hyperscaler_capex/"
    assert post.post_date is not None
    assert "Record backlog" in post.snippet
    # The search URL is subreddit-scoped with the query.
    assert "/r/datacenter/search.json" in transport.calls[0]
    assert "restrict_sr=1" in transport.calls[0]


def test_reddit_uses_descriptive_user_agent(tmp_path: Path):
    seen_headers = {}

    class HeaderCapture(FakeTransport):
        def get(self, url, headers):  # noqa: ANN001
            seen_headers.update(headers)
            return super().get(url, headers)

    transport = HeaderCapture({"/r/datacenter/": (200, reddit_payload([]))})
    RedditFetcher(tmp_path, transport=transport, sleep=lambda _s: None).fetch(
        "dc-capex", [("datacenter", "q")], WINDOW_END
    )
    assert "alphastock-sentiment" in seen_headers["User-Agent"]


def test_reddit_paces_between_requests(tmp_path: Path):
    transport = FakeTransport({"/r/datacenter/": (200, reddit_payload([]))})
    pauses: list[float] = []
    fetcher = RedditFetcher(tmp_path, transport=transport, pace_seconds=1.5, sleep=pauses.append)
    fetcher.fetch("dc-capex", [("datacenter", "q1"), ("datacenter", "q2")], WINDOW_END)
    assert pauses == [1.5]


def test_reddit_429_backs_off_then_succeeds(tmp_path: Path):
    class FlakyTransport(FakeTransport):
        def __init__(self):
            super().__init__({})
            self.failures_left = 2

        def get(self, url, headers):  # noqa: ANN001
            self.calls.append(url)
            if self.failures_left > 0:
                self.failures_left -= 1
                return 429, "rate limited"
            return 200, reddit_payload([])

    transport = FlakyTransport()
    pauses: list[float] = []
    fetcher = RedditFetcher(
        tmp_path, transport=transport, backoff_base_seconds=5.0, sleep=pauses.append
    )
    posts = fetcher.fetch("dc-capex", [("datacenter", "q")], WINDOW_END)
    assert posts == []
    assert len(transport.calls) == 3
    # Exponential backoff: base, base*2.
    assert pauses == [5.0, 10.0]


def test_reddit_persistent_429_raises(tmp_path: Path):
    transport = FakeTransport({"/r/datacenter/": (429, "rate limited")})
    fetcher = RedditFetcher(tmp_path, transport=transport, max_retries=2, sleep=lambda _s: None)
    with pytest.raises(FetchError, match="429"):
        fetcher.fetch("dc-capex", [("datacenter", "q")], WINDOW_END)


def test_reddit_cache_resume_second_run_fetches_nothing(tmp_path: Path):
    transport = FakeTransport(
        {"/r/datacenter/": (200, reddit_payload(["/r/datacenter/comments/abc/hyperscaler_capex/"]))}
    )
    fetcher = RedditFetcher(tmp_path, transport=transport, sleep=lambda _s: None)
    queries = [("datacenter", '"data center capex"')]

    first = fetcher.fetch("dc-capex", queries, WINDOW_END)
    assert len(first) == 1
    assert len(transport.calls) == 1

    # Same window: served entirely from cache.
    second = fetcher.fetch("dc-capex", queries, WINDOW_END)
    assert second == first
    assert len(transport.calls) == 1

    # A new week re-fetches — the rolling window is not served stale.
    fetcher.fetch("dc-capex", queries, date(2026, 11, 25))
    assert len(transport.calls) == 2


def test_reddit_malformed_payload_fails_loudly(tmp_path: Path):
    transport = FakeTransport({"/r/datacenter/": (200, "<html>blocked</html>")})
    fetcher = RedditFetcher(tmp_path, transport=transport, sleep=lambda _s: None)
    with pytest.raises(FetchError, match="malformed"):
        fetcher.fetch("dc-capex", [("datacenter", "q")], WINDOW_END)


def test_youcom_requires_api_key(tmp_path: Path):
    with pytest.raises(FetchError, match="API key"):
        YouComFetcher("", tmp_path)


def test_youcom_scopes_queries_per_platform(tmp_path: Path):
    transport = FakeTransport(
        {
            "ydc-index.io": (
                200,
                ydc_payload(
                    [
                        {
                            "url": "https://x.com/gridwatch/status/123",
                            "title": "Hyperscaler capex up again",
                            "description": "Record demand.",
                            "page_age": "2026-11-16T10:00:00Z",
                        }
                    ]
                ),
            )
        }
    )
    fetcher = YouComFetcher("test-key", tmp_path, transport=transport, sleep=lambda _s: None)
    posts = fetcher.fetch(
        "dc-capex",
        "x-indexed",
        ["data center capex (site:x.com OR site:twitter.com)"],
        WINDOW_END,
    )

    assert "site%3Ax.com" in transport.calls[0]
    assert "freshness=2026-11-11to2026-11-18" in transport.calls[0]
    assert "test-key" not in str(posts)  # the key never leaks into output
    assert posts[0].platform == "x-indexed"
    assert posts[0].url == "https://x.com/gridwatch/status/123"
    assert posts[0].post_date == "2026-11-16"


def test_youcom_rejects_unknown_platform(tmp_path: Path):
    fetcher = YouComFetcher("test-key", tmp_path, sleep=lambda _s: None)
    with pytest.raises(FetchError, match="unknown indexed platform"):
        fetcher.fetch("dc-capex", "reddit", ["q"], WINDOW_END)
