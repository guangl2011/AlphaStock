"""Reddit via the treg.to gateway — no test touches the network (or bills credits).

Endpoint and dialect are the live-pinned contract verified 2026-11-18:
GET https://treg.to/call/scrapecreators.reddit.search.posts with X-Treg-Token
answers the provider's raw shape with treg billing counters injected —
{success: bool, posts: [...], after: str|null, credits_charged,
credits_remaining} — no output.found/output.data wrapper. The subreddit
parameter is ignored server-side (site-wide search); limit is not honored
(fixed 25-row pages). All fixture authors, URLs, and ids are synthetic.
"""

import json
import urllib.parse
from datetime import date
from pathlib import Path

import pytest

from pipelines.sentiment.fetchers import (
    REDDIT_TREG_ENDPOINT,
    REDDIT_TREG_MAX_PAGES,
    REDDIT_TREG_PAGE_SIZE,
    FetchError,
    RedditTregFetcher,
)
from pipelines.sentiment.matrix import load_matrix
from pipelines.sentiment.run_scan import ScanOptions, run_scan

WINDOW_END = date(2026, 11, 18)
TOKEN = "test-treg-token"


class FakeRedditTregTransport:
    """Records every GET and answers pages in order; never touches the network."""

    def __init__(self, pages: list[tuple[int, str]]):
        self.pages = pages
        self.calls: list[str] = []
        self.get_headers: list[dict[str, str]] = []

    def get(self, url: str, headers: dict[str, str]) -> tuple[int, str]:
        index = len(self.calls)
        self.calls.append(url)
        self.get_headers.append(headers)
        if index < len(self.pages):
            return self.pages[index]
        return self.pages[-1]

    def post(
        self, url: str, headers: dict[str, str], body: str, content_type: str
    ) -> tuple[int, str]:  # pragma: no cover - the Reddit endpoint is GET-only
        return 400, "reddit fetcher only GETs"


# --- fixtures built from the pinned raw-dialect shapes (sanitized) -------------


def reddit_row(number: int = 0, **overrides) -> dict:
    """One scrapecreators.reddit.search.posts row with every pinned key present."""
    row = {
        "id": f"abc{number}",
        "post_id": f"abc{number}",
        "subreddit": "datacenter",
        "permalink": f"/r/datacenter/comments/abc{number}/hyperscaler_capex/",
        "author": "gridnerd_88",
        "title": "Hyperscaler capex guides up again",
        "selftext": "Record backlog and fully booked capacity.",
        "created_utc": 1769904000,
        "score": 42,
        "num_comments": 7,
        "upvote_ratio": 0.97,
    }
    row.update(overrides)
    return row


def raw_page(rows: list[dict], after: str | None = None) -> str:
    """The pinned raw dialect: provider rows plus treg billing counters."""
    return json.dumps(
        {
            "success": True,
            "posts": rows,
            "after": after,
            "credits_charged": 1,
            "credits_remaining": 1000,
        }
    )


def fetcher(tmp_path: Path, transport, **kwargs) -> RedditTregFetcher:
    return RedditTregFetcher(
        tmp_path, token=TOKEN, transport=transport, sleep=lambda _s: None, **kwargs
    )


def query_of(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()}


# --- credential handling ------------------------------------------------------


def test_missing_treg_token_fails_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TREG_TOKEN", raising=False)
    monkeypatch.delenv("TREG_MAX_COST", raising=False)
    with pytest.raises(FetchError) as excinfo:
        RedditTregFetcher(tmp_path)
    message = str(excinfo.value)
    assert "TREG_TOKEN" in message
    assert "https://treg.to" in message
    assert "Settings" in message  # where to set it in GitHub


def test_empty_treg_token_fails_loudly(tmp_path: Path):
    with pytest.raises(FetchError, match="TREG_TOKEN"):
        RedditTregFetcher(tmp_path, token="")


def test_request_is_get_with_x_treg_token_and_no_authorization(tmp_path: Path):
    transport = FakeRedditTregTransport([(200, raw_page([]))])
    fetcher(tmp_path, transport).fetch("dc-capex", [("datacenter", "q")], WINDOW_END)

    url, headers = transport.calls[0], transport.get_headers[0]
    assert url.startswith(f"https://treg.to/call/{REDDIT_TREG_ENDPOINT}?")
    assert headers["X-Treg-Token"] == TOKEN
    assert "Authorization" not in headers


def test_request_carries_query_sort_new_and_window_tier(tmp_path: Path):
    transport = FakeRedditTregTransport([(200, raw_page([]))])
    fetcher(tmp_path, transport).fetch(
        "dc-capex", [("datacenter", '"data center capex"')], WINDOW_END, t="month"
    )

    params = query_of(transport.calls[0])
    assert params["query"] == '"data center capex"'
    assert params["sort"] == "new"
    assert params["t"] == "month"


def test_limit_is_never_sent_page_size_is_server_fixed(tmp_path: Path):
    transport = FakeRedditTregTransport([(200, raw_page([]))])
    fetcher(tmp_path, transport).fetch("dc-capex", [("datacenter", "q")], WINDOW_END)

    assert "limit" not in query_of(transport.calls[0])


def test_subreddit_is_not_sent_site_wide_only(tmp_path: Path):
    transport = FakeRedditTregTransport([(200, raw_page([]))])
    fetcher(tmp_path, transport).fetch("dc-capex", [("datacenter", "q")], WINDOW_END)

    assert "subreddit" not in query_of(transport.calls[0])


def test_non_200_fails_loudly(tmp_path: Path):
    transport = FakeRedditTregTransport([(403, '{"error": "bad token"}')])
    with pytest.raises(FetchError, match="403"):
        fetcher(tmp_path, transport).fetch("dc-capex", [("datacenter", "q")], WINDOW_END)


def test_5xx_backs_off_then_succeeds(tmp_path: Path):
    transport = FakeRedditTregTransport(
        [(503, "unavailable"), (502, "gateway"), (200, raw_page([]))]
    )
    pauses: list[float] = []
    f = RedditTregFetcher(tmp_path, token=TOKEN, transport=transport, sleep=pauses.append)
    posts = f.fetch("dc-capex", [("datacenter", "q")], WINDOW_END)
    assert posts == []
    assert len(transport.calls) == 3
    assert pauses == [5.0, 10.0]  # exponential backoff, matching the repo fetch convention


# --- (subreddit, query) pair dedupe -------------------------------------------


def test_call_count_equals_unique_queries_not_pairs(tmp_path: Path):
    transport = FakeRedditTregTransport([(200, raw_page([]))])
    pairs = [("datacenter", "same query"), ("stocks", "same query"), ("datacenter", "other")]
    fetcher(tmp_path, transport).fetch("dc-capex", pairs, WINDOW_END)

    # Three pairs, two unique queries — the subreddit dimension is spent.
    assert len(transport.calls) == 2
    sent = [query_of(url)["query"] for url in transport.calls]
    assert sent == ["same query", "other"]


def test_pair_dedupe_preserves_first_seen_order(tmp_path: Path):
    transport = FakeRedditTregTransport([(200, raw_page([]))])
    pairs = [("stocks", "b query"), ("datacenter", "a query"), ("stocks", "b query")]
    fetcher(tmp_path, transport).fetch("dc-capex", pairs, WINDOW_END)

    assert [query_of(url)["query"] for url in transport.calls] == ["b query", "a query"]


# --- pagination ---------------------------------------------------------------


def test_after_cursor_from_page_n_appears_in_request_n_plus_one(tmp_path: Path):
    page_one = raw_page([reddit_row(1)], after="t3_cursor1")
    page_two = raw_page([reddit_row(2)], after=None)
    transport = FakeRedditTregTransport([(200, page_one), (200, page_two)])
    posts = fetcher(tmp_path, transport).fetch("dc-capex", [("datacenter", "q")], WINDOW_END)

    assert len(transport.calls) == 2
    assert "after" not in query_of(transport.calls[0])
    assert query_of(transport.calls[1])["after"] == "t3_cursor1"
    assert [p.url for p in posts] == [
        "https://www.reddit.com/r/datacenter/comments/abc1/hyperscaler_capex/",
        "https://www.reddit.com/r/datacenter/comments/abc2/hyperscaler_capex/",
    ]


def test_pagination_stops_at_the_page_cap(tmp_path: Path):
    never_ending = raw_page([reddit_row()], after="t3_next")
    transport = FakeRedditTregTransport([(200, never_ending)])
    posts = fetcher(tmp_path, transport).fetch("dc-capex", [("datacenter", "q")], WINDOW_END)

    assert len(transport.calls) == REDDIT_TREG_MAX_PAGES
    for index in range(1, REDDIT_TREG_MAX_PAGES):
        assert query_of(transport.calls[index])["after"] == "t3_next"
    # Every page returns the same permalink: URL dedupe keeps exactly one post.
    assert len(posts) == 1


def test_page_size_constant_documents_the_server_fixed_25():
    assert REDDIT_TREG_PAGE_SIZE == 25


def test_window_tier_is_part_of_the_cache_key(tmp_path: Path):
    transport = FakeRedditTregTransport([(200, raw_page([]))])
    f = fetcher(tmp_path, transport)
    f.fetch("dc-capex", [("datacenter", "q")], WINDOW_END, t="week")
    f.fetch("dc-capex", [("datacenter", "q")], WINDOW_END, t="month")
    # A widened window tier re-fetches — week-tier cache is not served stale.
    assert len(transport.calls) == 2


def test_cache_resume_second_run_fetches_nothing(tmp_path: Path):
    transport = FakeRedditTregTransport([(200, raw_page([reddit_row(1)]))])
    f = fetcher(tmp_path, transport)
    pairs = [("datacenter", "q")]

    first = f.fetch("dc-capex", pairs, WINDOW_END)
    assert len(first) == 1
    assert len(transport.calls) == 1

    second = f.fetch("dc-capex", pairs, WINDOW_END)
    assert second == first
    assert len(transport.calls) == 1  # served entirely from cache

    # A new week re-fetches — the rolling window is not served stale.
    f.fetch("dc-capex", pairs, date(2026, 11, 25))
    assert len(transport.calls) == 2


# --- row mapping --------------------------------------------------------------


def test_rows_map_to_the_reddit_post_shape(tmp_path: Path):
    transport = FakeRedditTregTransport([(200, raw_page([reddit_row(7)]))])
    posts = fetcher(tmp_path, transport).fetch("dc-capex", [("datacenter", "q")], WINDOW_END)

    assert len(posts) == 1
    post = posts[0]
    assert post.platform == "reddit"
    assert post.author == "gridnerd_88"
    assert post.url == "https://www.reddit.com/r/datacenter/comments/abc7/hyperscaler_capex/"
    assert post.post_date == "2026-02-01"  # from created_utc 1769904000 (unix seconds)
    assert "Hyperscaler capex" in post.snippet
    assert "Record backlog" in post.snippet
    # Engagement proxy per PR #30 semantics: score + num_comments.
    assert post.engagement == pytest.approx(42 + 7)


@pytest.mark.parametrize(
    ("permalink", "expected"),
    [
        ("/r/stocks/comments/abc/title/", "https://www.reddit.com/r/stocks/comments/abc/title/"),
        (
            "//www.reddit.com/r/stocks/comments/abc/title/",
            "https://www.reddit.com/r/stocks/comments/abc/title/",
        ),
        (
            "https://www.reddit.com/r/stocks/comments/abc/title/",
            "https://www.reddit.com/r/stocks/comments/abc/title/",
        ),
        (
            "http://www.reddit.com/r/stocks/comments/abc/title/",
            "http://www.reddit.com/r/stocks/comments/abc/title/",
        ),
    ],
)
def test_permalink_scheme_handling(tmp_path: Path, permalink: str, expected: str):
    transport = FakeRedditTregTransport([(200, raw_page([reddit_row(permalink=permalink)]))])
    posts = fetcher(tmp_path, transport).fetch("dc-capex", [("datacenter", "q")], WINDOW_END)
    assert posts[0].url == expected


def test_selftext_only_post_maps_title_plus_body(tmp_path: Path):
    transport = FakeRedditTregTransport(
        [(200, raw_page([reddit_row(title="Headline only", selftext="")]))]
    )
    posts = fetcher(tmp_path, transport).fetch("dc-capex", [("datacenter", "q")], WINDOW_END)
    assert posts[0].snippet.startswith("Headline only")


# --- raw-dialect hardening -----------------------------------------------------


def malformed_cases() -> dict[str, tuple[str, str]]:
    """Each malformed page paired with the path fragment the error must name."""
    good = raw_page([reddit_row()])
    no_success = json.loads(good)
    del no_success["success"]
    success_not_bool = json.loads(good)
    success_not_bool["success"] = "yes"
    success_false = json.loads(good)
    success_false["success"] = False
    no_posts = json.loads(good)
    del no_posts["posts"]
    posts_not_list = json.loads(good)
    posts_not_list["posts"] = {"oops": 1}
    row_not_object = json.loads(good)
    row_not_object["posts"] = ["not-an-object"]
    row_missing_key = json.loads(good)
    del row_missing_key["posts"][0]["permalink"]
    numeric_not_number = json.loads(good)
    numeric_not_number["posts"][0]["score"] = "many"
    after_not_string = json.loads(good)
    after_not_string["after"] = 17
    return {
        "no_success": (json.dumps(no_success), "success"),
        "success_not_bool": (json.dumps(success_not_bool), "success"),
        "success_false": (json.dumps(success_false), "success=false"),
        "no_posts": (json.dumps(no_posts), "posts"),
        "posts_not_list": (json.dumps(posts_not_list), "posts"),
        "row_not_object": (json.dumps(row_not_object), "posts[0]"),
        "row_missing_permalink": (json.dumps(row_missing_key), "permalink"),
        "numeric_not_number": (json.dumps(numeric_not_number), "score"),
        "after_not_string": (json.dumps(after_not_string), "after"),
    }


@pytest.mark.parametrize("case", list(malformed_cases()))
def test_malformed_raw_dialect_fails_loudly_naming_endpoint_and_path(tmp_path: Path, case: str):
    body, path = malformed_cases()[case]
    transport = FakeRedditTregTransport([(200, body)])
    with pytest.raises(FetchError) as excinfo:
        fetcher(tmp_path, transport).fetch("dc-capex", [("datacenter", "q")], WINDOW_END)
    message = str(excinfo.value)
    assert REDDIT_TREG_ENDPOINT in message
    assert "malformed" in message or "unsuccessful" in message
    assert path in message


def test_non_json_success_body_fails_loudly(tmp_path: Path):
    transport = FakeRedditTregTransport([(200, "<html>nope</html>")])
    with pytest.raises(FetchError, match="malformed"):
        fetcher(tmp_path, transport).fetch("dc-capex", [("datacenter", "q")], WINDOW_END)


def test_null_score_still_fails_the_row(tmp_path: Path):
    """The indexed endpoints' null-engagement tolerance does not extend to the
    Reddit raw dialect: its score/num_comments contract is unchanged."""
    transport = FakeRedditTregTransport([(200, raw_page([reddit_row(1, score=None)]))])
    with pytest.raises(FetchError, match=r"score.*expected a number"):
        fetcher(tmp_path, transport).fetch("dc-capex", [("datacenter", "q")], WINDOW_END)


def test_success_true_with_empty_posts_is_an_honest_empty_page(tmp_path: Path):
    transport = FakeRedditTregTransport([(200, raw_page([]))])
    posts = fetcher(tmp_path, transport).fetch("dc-capex", [("datacenter", "q")], WINDOW_END)
    assert posts == []
    assert len(transport.calls) == 1


# --- coverage accounting -------------------------------------------------------


def test_coverage_counters_track_unique_queries_and_pages(tmp_path: Path):
    pages = [
        (200, raw_page([reddit_row(1)], after="t3_a")),
        (200, raw_page([reddit_row(2)], after=None)),
        (200, raw_page([reddit_row(3)], after=None)),
    ]
    transport = FakeRedditTregTransport(pages)
    f = fetcher(tmp_path, transport)
    f.fetch("dc-capex", [("a", "q1"), ("b", "q1"), ("c", "q2")], WINDOW_END)

    assert f.queries_fetched == 2  # unique queries, not pairs
    assert f.pages_fetched == 3  # two pages for q1, one for q2


# --- run_scan wiring -----------------------------------------------------------


def test_run_scan_reports_unique_queries_and_pages_for_reddit(tmp_path: Path):
    transport = FakeRedditTregTransport([(200, raw_page([reddit_row(1)], after=None))])
    reddit_fetcher = fetcher(tmp_path, transport)
    options = ScanOptions(
        platforms=("reddit",),
        topic_ids=("dc-capex",),
        as_of="2026-11-18T12:00:00Z",
        cache_dir=tmp_path / "cache",
        out_dir=tmp_path / "out",
    )
    _, payload = run_scan(load_matrix(), options, reddit=reddit_fetcher)

    coverage = payload["coverage"]["reddit"]
    assert coverage["unique_queries"] >= 1
    assert coverage["pages_fetched"] >= 1


# --- one bad page degrades, it does not abort ----------------------------------


class CursorPagesFailTransport:
    """First pages answer with a unique row + cursor; every after-cursor page 500s.

    Mirrors the live 2026-11-19 incident: upstream GraphQL errors on one
    page-2 cursor after retries, while every first page succeeded.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.pages_served = 0

    def get(self, url: str, headers: dict[str, str]) -> tuple[int, str]:
        self.calls.append(url)
        if "after=" in url:
            return 500, "Reddit guest search returned GraphQL errors"
        self.pages_served += 1
        return 200, raw_page([reddit_row(self.pages_served)], after="t3_next")

    def post(
        self, url: str, headers: dict[str, str], body: str, content_type: str
    ) -> tuple[int, str]:  # pragma: no cover - the Reddit endpoint is GET-only
        return 400, "reddit fetcher only GETs"


def scan_options(tmp_path: Path, platforms: tuple[str, ...] = ("reddit",)) -> ScanOptions:
    return ScanOptions(
        platforms=platforms,
        topic_ids=("dc-capex",),
        as_of="2026-11-18T12:00:00Z",
        cache_dir=tmp_path / "cache",
        out_dir=tmp_path / "out",
    )


def test_persistent_500_on_one_page_degrades_to_shortfall(tmp_path: Path):
    transport = FakeRedditTregTransport(
        [(200, raw_page([reddit_row(1)], after="t3_c1")), (500, "boom")]
    )
    f = fetcher(tmp_path, transport, max_retries=0)

    posts = f.fetch("dc-capex", [("datacenter", "q")], WINDOW_END)

    # Page 1's posts are kept; the failed page 2 stops only this query.
    assert [p.url for p in posts] == [
        "https://www.reddit.com/r/datacenter/comments/abc1/hyperscaler_capex/"
    ]
    assert f.pages_fetched == 1
    assert f.successful_pages[REDDIT_TREG_ENDPOINT] == 1
    assert f.failed_pages[REDDIT_TREG_ENDPOINT] == 1
    assert f.failed_queries[REDDIT_TREG_ENDPOINT] == 1


def test_failed_page_does_not_block_remaining_queries(tmp_path: Path):
    transport = FakeRedditTregTransport([(200, raw_page([reddit_row(1)])), (500, "boom")])
    f = fetcher(tmp_path, transport, max_retries=0)

    posts = f.fetch("dc-capex", [("a", "q1"), ("b", "q2")], WINDOW_END)

    assert len(posts) == 1
    assert len(transport.calls) == 2  # q2 was still attempted
    assert f.failed_queries[REDDIT_TREG_ENDPOINT] == 1
    assert f.successful_pages[REDDIT_TREG_ENDPOINT] == 1


def test_all_first_page_failures_fail_fast_as_today(tmp_path: Path):
    transport = FakeRedditTregTransport([(500, "down")])
    f = fetcher(tmp_path, transport, max_retries=0)

    # Nothing banked when the first page fails: a dead platform fails loudly
    # (and cheaply — no second query is attempted), never an all-empty batch.
    with pytest.raises(FetchError, match="reddit fetch failed"):
        f.fetch("dc-capex", [("a", "q1"), ("b", "q2")], WINDOW_END)
    assert len(transport.calls) == 1


def test_run_scan_aborts_when_every_reddit_page_fails(tmp_path: Path):
    transport = FakeRedditTregTransport([(500, "down")])
    reddit_fetcher = fetcher(tmp_path, transport, max_retries=0)

    with pytest.raises(FetchError, match="reddit fetch failed"):
        run_scan(load_matrix(), scan_options(tmp_path), reddit=reddit_fetcher)


def test_run_scan_completes_when_after_cursor_pages_fail(tmp_path: Path):
    # The live incident: every page-2 cursor 500s after retries, but all first
    # pages answer. The scan must complete and report the shortfall honestly.
    transport = CursorPagesFailTransport()
    reddit_fetcher = fetcher(tmp_path, transport, max_retries=0)

    run_path, payload = run_scan(load_matrix(), scan_options(tmp_path), reddit=reddit_fetcher)

    coverage = payload["coverage"]["reddit"]
    assert coverage["kept"] > 0  # every first page answered and is retained
    assert coverage["failed_pages"] >= 1
    assert coverage["failed_queries"] >= 1
    assert coverage["failed_endpoints"] == {REDDIT_TREG_ENDPOINT: coverage["failed_pages"]}
    assert run_path.is_file()
    assert len(payload["topics"]) == 1
