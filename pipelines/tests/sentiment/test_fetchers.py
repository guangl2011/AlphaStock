"""Fetchers against recorded responses — no test touches the network."""

import base64
import json
from datetime import date
from pathlib import Path

import pytest

from pipelines.sentiment.fetchers import (
    REDDIT_MAX_PAGES,
    REDDIT_PAGE_LIMIT,
    FetchError,
    RedditFetcher,
)

WINDOW_END = date(2026, 11, 18)
CLIENT_ID = "test-client-id"
CLIENT_SECRET = "test-client-secret"


class FakeTransport:
    """Recorded responses keyed by URL substring; records every call."""

    def __init__(self, responses: dict[str, tuple[int, str]]):
        self.responses = responses
        self.calls: list[str] = []
        self.get_headers: list[dict[str, str]] = []
        self.post_headers: list[dict[str, str]] = []
        self.post_bodies: list[str] = []

    def get(self, url: str, headers: dict[str, str]) -> tuple[int, str]:
        self.calls.append(url)
        self.get_headers.append(headers)
        for fragment, response in self.responses.items():
            if fragment in url:
                return response
        return 200, "{}"

    def post(
        self, url: str, headers: dict[str, str], body: str, content_type: str
    ) -> tuple[int, str]:
        self.calls.append(f"POST {url}")
        self.post_headers.append(headers)
        self.post_bodies.append(body)
        for fragment, response in self.responses.items():
            if fragment in f"POST {url}":
                return response
        return 200, token_payload()


def token_payload(token: str = "test-token", expires_in: float = 3600.0) -> str:
    return json.dumps(
        {"access_token": token, "token_type": "bearer", "expires_in": expires_in}
    )


def reddit_payload(permalinks: list[str], after: str | None = None) -> str:
    data: dict = {
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
    if after is not None:
        data["after"] = after
    return json.dumps({"data": data})


def oauth_fetcher(tmp_path: Path, transport, **kwargs) -> RedditFetcher:
    """A fetcher with test credentials; tests pass transport and pacing fakes."""
    return RedditFetcher(
        tmp_path,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        transport=transport,
        **kwargs,
    )


def test_reddit_recorded_response_parses_posts(tmp_path: Path):
    transport = FakeTransport(
        {"/r/datacenter/": (200, reddit_payload(["/r/datacenter/comments/abc/hyperscaler_capex/"]))}
    )
    fetcher = oauth_fetcher(tmp_path, transport, sleep=lambda _s: None)
    posts = fetcher.fetch("dc-capex", [("datacenter", '"data center capex"')], WINDOW_END)

    assert len(posts) == 1
    post = posts[0]
    assert post.platform == "reddit"
    assert post.author == "gridnerd_88"
    assert post.url == "https://www.reddit.com/r/datacenter/comments/abc/hyperscaler_capex/"
    assert post.post_date is not None
    assert "Record backlog" in post.snippet
    # The search URL is subreddit-scoped with the query, over the OAuth host.
    assert "https://oauth.reddit.com/r/datacenter/search.json" in transport.calls[1]
    assert "restrict_sr=1" in transport.calls[1]


def test_reddit_token_request_uses_basic_auth_and_bearer_search_header(tmp_path: Path):
    transport = FakeTransport({"/r/datacenter/": (200, reddit_payload([]))})
    fetcher = oauth_fetcher(tmp_path, transport, sleep=lambda _s: None)
    fetcher.fetch("dc-capex", [("datacenter", "q")], WINDOW_END)

    # Token: POSTed to the OAuth token endpoint with client_credentials basic auth.
    assert transport.calls[0] == "POST https://www.reddit.com/api/v1/access_token"
    assert transport.post_bodies[0] == "grant_type=client_credentials"
    expected_basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    assert transport.post_headers[0]["Authorization"] == f"Basic {expected_basic}"
    assert "alphastock-sentiment" in transport.post_headers[0]["User-Agent"]

    # Search: Bearer token plus the descriptive User-Agent, same query params.
    search_headers = transport.get_headers[0]
    assert search_headers["Authorization"] == "Bearer test-token"
    assert "alphastock-sentiment" in search_headers["User-Agent"]
    assert "limit=100" in transport.calls[1]


def test_reddit_token_is_cached_until_expiry_margin(tmp_path: Path):
    transport = FakeTransport({"/r/datacenter/": (200, reddit_payload([]))})
    now = [1000.0]
    fetcher = oauth_fetcher(tmp_path, transport, sleep=lambda _s: None, clock=lambda: now[0])

    fetcher.fetch("dc-capex", [("datacenter", "q")], WINDOW_END)
    assert len(transport.post_headers) == 1

    # Within (expiry - 60s): the same token is reused, no second token request.
    now[0] = 1000.0 + 3600.0 - 60.0 - 1.0
    fetcher.fetch("dc-capex", [("datacenter", "q")], WINDOW_END)
    assert len(transport.post_headers) == 1

    # Inside the 60-second margin (past expiry - 60): a fresh token is fetched.
    now[0] = 1000.0 + 3600.0 - 60.0
    fetcher.fetch("dc-capex", [("datacenter", "q")], WINDOW_END)
    assert len(transport.post_headers) == 2


def test_reddit_refreshes_token_once_on_401(tmp_path: Path):
    class ExpiringToken(FakeTransport):
        def __init__(self):
            super().__init__({})
            self.gets = 0

        def get(self, url, headers):  # noqa: ANN001
            self.gets += 1
            if self.gets == 1:
                return 401, '{"message": "Unauthorized"}'
            return 200, reddit_payload([])

    transport = ExpiringToken()
    fetcher = oauth_fetcher(tmp_path, transport, sleep=lambda _s: None)
    posts = fetcher.fetch("dc-capex", [("datacenter", "q")], WINDOW_END)

    assert posts == []
    # One token request at construction of the request, one refresh after the 401.
    assert len(transport.post_headers) == 2
    assert transport.gets == 2


def test_reddit_persistent_401_fails_loudly(tmp_path: Path):
    transport = FakeTransport({"/r/datacenter/": (401, '{"message": "Unauthorized"}')})
    fetcher = oauth_fetcher(tmp_path, transport, max_retries=2, sleep=lambda _s: None)
    with pytest.raises(FetchError, match="401"):
        fetcher.fetch("dc-capex", [("datacenter", "q")], WINDOW_END)
    # Exactly one refresh: the retried request is not refreshed again.
    assert len(transport.post_headers) == 2


def test_reddit_token_fetch_failure_is_loud(tmp_path: Path):
    transport = FakeTransport({"access_token": (403, "forbidden")})
    fetcher = oauth_fetcher(tmp_path, transport, sleep=lambda _s: None)
    with pytest.raises(FetchError, match="token fetch failed"):
        fetcher.fetch("dc-capex", [("datacenter", "q")], WINDOW_END)


def test_reddit_malformed_token_payload_fails_loudly(tmp_path: Path):
    transport = FakeTransport({"access_token": (200, "<html>nope</html>")})
    fetcher = oauth_fetcher(tmp_path, transport, sleep=lambda _s: None)
    with pytest.raises(FetchError, match="malformed"):
        fetcher.fetch("dc-capex", [("datacenter", "q")], WINDOW_END)


def test_reddit_missing_credentials_fail_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for var in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_ALLOW_ANONYMOUS"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(FetchError) as excinfo:
        RedditFetcher(tmp_path)
    message = str(excinfo.value)
    assert "REDDIT_CLIENT_ID" in message
    assert "REDDIT_CLIENT_SECRET" in message
    assert "https://www.reddit.com/prefs/apps" in message
    assert "Settings" in message  # where to set them in GitHub


def test_reddit_anonymous_mode_requires_explicit_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("REDDIT_ALLOW_ANONYMOUS", "1")
    transport = FakeTransport({"/r/datacenter/": (200, reddit_payload([]))})
    RedditFetcher(tmp_path, transport=transport, sleep=lambda _s: None).fetch(
        "dc-capex", [("datacenter", "q")], WINDOW_END
    )
    # Anonymous mode: public host, no Authorization header, no token request.
    assert "https://www.reddit.com/r/datacenter/search.json" in transport.calls[0]
    assert "Authorization" not in transport.get_headers[0]
    assert not transport.post_headers


def test_reddit_paginates_through_the_after_cursor(tmp_path: Path):
    page_one = reddit_payload(
        [f"/r/datacenter/comments/p1/{i}/" for i in range(REDDIT_PAGE_LIMIT)], after="t3_cursor1"
    )
    page_two = reddit_payload([f"/r/datacenter/comments/p2/{i}/" for i in range(REDDIT_PAGE_LIMIT)])
    transport = FakeTransport(
        {
            "after=t3_cursor1": (200, page_two),
            "/r/datacenter/": (200, page_one),
        }
    )
    fetcher = oauth_fetcher(tmp_path, transport, sleep=lambda _s: None)
    posts = fetcher.fetch("dc-capex", [("datacenter", '"data center capex"')], WINDOW_END)

    # Page 1 fetched, cursor followed, page 2 ends pagination.
    assert len(transport.calls) == 3  # token POST + two search pages
    assert "limit=100" in transport.calls[1]
    assert "after=t3_cursor1" in transport.calls[2]
    assert len(posts) == 2 * REDDIT_PAGE_LIMIT  # no URL overlap between pages


def test_reddit_stops_at_three_pages_per_query(tmp_path: Path):
    never_ending = reddit_payload(
        [f"/r/datacenter/comments/p/{i}/" for i in range(REDDIT_PAGE_LIMIT)], after="t3_next"
    )
    transport = FakeTransport(
        {"/r/datacenter/": (200, never_ending), "after=": (200, never_ending)}
    )
    fetcher = oauth_fetcher(tmp_path, transport, sleep=lambda _s: None)
    posts = fetcher.fetch("dc-capex", [("datacenter", "q")], WINDOW_END)

    search_calls = [call for call in transport.calls if not call.startswith("POST")]
    assert len(search_calls) == REDDIT_MAX_PAGES
    # All pages return the same permalinks: URL dedupe keeps exactly one page's worth.
    assert len(posts) == REDDIT_PAGE_LIMIT


def test_reddit_window_tier_is_part_of_the_cache_key(tmp_path: Path):
    transport = FakeTransport({"/r/datacenter/": (200, reddit_payload([]))})
    fetcher = oauth_fetcher(tmp_path, transport, sleep=lambda _s: None)
    fetcher.fetch("dc-capex", [("datacenter", "q")], WINDOW_END, t="week")
    fetcher.fetch("dc-capex", [("datacenter", "q")], WINDOW_END, t="month")
    # A widened window tier re-fetches — week-tier cache is not served stale.
    assert len([c for c in transport.calls if not c.startswith("POST")]) == 2


def test_reddit_uses_descriptive_user_agent(tmp_path: Path):
    transport = FakeTransport({"/r/datacenter/": (200, reddit_payload([]))})
    oauth_fetcher(tmp_path, transport, sleep=lambda _s: None).fetch(
        "dc-capex", [("datacenter", "q")], WINDOW_END
    )
    assert "alphastock-sentiment" in transport.get_headers[0]["User-Agent"]


def test_reddit_paces_between_requests(tmp_path: Path):
    transport = FakeTransport({"/r/datacenter/": (200, reddit_payload([]))})
    pauses: list[float] = []
    fetcher = oauth_fetcher(tmp_path, transport, pace_seconds=1.5, sleep=pauses.append)
    fetcher.fetch("dc-capex", [("datacenter", "q1"), ("datacenter", "q2")], WINDOW_END)
    assert pauses == [1.5]


def test_reddit_429_backs_off_then_succeeds(tmp_path: Path):
    class FlakyTransport(FakeTransport):
        def __init__(self):
            super().__init__({})
            self.failures_left = 2

        def get(self, url, headers):  # noqa: ANN001
            self.calls.append(url)
            self.get_headers.append(headers)
            if self.failures_left > 0:
                self.failures_left -= 1
                return 429, "rate limited"
            return 200, reddit_payload([])

    transport = FlakyTransport()
    pauses: list[float] = []
    fetcher = oauth_fetcher(
        tmp_path, transport, backoff_base_seconds=5.0, sleep=pauses.append
    )
    posts = fetcher.fetch("dc-capex", [("datacenter", "q")], WINDOW_END)
    assert posts == []
    assert len(transport.get_headers) == 3
    # Exponential backoff: base, base*2.
    assert pauses == [5.0, 10.0]


def test_reddit_persistent_429_raises(tmp_path: Path):
    transport = FakeTransport({"/r/datacenter/": (429, "rate limited")})
    fetcher = oauth_fetcher(tmp_path, transport, max_retries=2, sleep=lambda _s: None)
    with pytest.raises(FetchError, match="429"):
        fetcher.fetch("dc-capex", [("datacenter", "q")], WINDOW_END)


def test_reddit_cache_resume_second_run_fetches_nothing(tmp_path: Path):
    transport = FakeTransport(
        {"/r/datacenter/": (200, reddit_payload(["/r/datacenter/comments/abc/hyperscaler_capex/"]))}
    )
    fetcher = oauth_fetcher(tmp_path, transport, sleep=lambda _s: None)
    queries = [("datacenter", '"data center capex"')]

    first = fetcher.fetch("dc-capex", queries, WINDOW_END)
    assert len(first) == 1
    assert len([c for c in transport.calls if not c.startswith("POST")]) == 1

    # Same window: served entirely from cache.
    second = fetcher.fetch("dc-capex", queries, WINDOW_END)
    assert second == first
    assert len([c for c in transport.calls if not c.startswith("POST")]) == 1

    # A new week re-fetches — the rolling window is not served stale.
    fetcher.fetch("dc-capex", queries, date(2026, 11, 25))
    assert len([c for c in transport.calls if not c.startswith("POST")]) == 2
def test_reddit_malformed_payload_fails_loudly(tmp_path: Path):
    transport = FakeTransport({"/r/datacenter/": (200, "<html>blocked</html>")})
    fetcher = oauth_fetcher(tmp_path, transport, sleep=lambda _s: None)
    with pytest.raises(FetchError, match="malformed"):
        fetcher.fetch("dc-capex", [("datacenter", "q")], WINDOW_END)

