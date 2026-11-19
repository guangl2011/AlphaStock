"""treg.to gateway fetchers against the pinned envelope — no test touches the network.

Envelope and row shapes are the live-pinned contract verified 2026-11-18:
POST https://treg.to/call/{endpoint_id} with X-Treg-Token answers
{provider, resultId, replayed, costUsd, output: {found, data}} where data
carries the rows under ``items`` (X) / ``posts`` (LinkedIn) plus nextCursor.
All fixture authors, URLs, and ids are synthetic.
"""

import json
import logging
from datetime import date, timedelta
from pathlib import Path

import pytest

from pipelines.sentiment.fetchers import (
    TREG_ENDPOINT,
    TREG_QUERY_CHUNK_MAX_CHARS,
    TREG_ROWS_KEY,
    FetchError,
    PageFetchError,
    TransportError,
    TregFetcher,
    _cache_path,
    chunk_queries,
    linkedin_date_posted,
)
from pipelines.sentiment.matrix import load_matrix
from pipelines.sentiment.run_scan import ScanOptions, run_scan

WINDOW_END = date(2026, 11, 18)
TOKEN = "test-treg-token"


class AlwaysTransportErrorTransport:
    """Every POST raises TransportError — a persistent network failure."""

    def post(self, url: str, headers: dict, body: str, content_type: str) -> tuple[int, str]:
        raise TransportError("connection refused")

    def get(self, url: str, headers: dict) -> tuple[int, str]:  # pragma: no cover - unused
        return 500, "treg fetcher only POSTs"


class FakeTregTransport:
    """Records every POST and answers pages in order; never touches the network."""

    def __init__(self, pages: list[tuple[int, str]]):
        self.pages = pages
        self.calls: list[tuple[str, dict, dict]] = []  # (url, headers, parsed body)

    def post(self, url: str, headers: dict, body: str, content_type: str) -> tuple[int, str]:
        index = len(self.calls)
        self.calls.append((url, headers, json.loads(body)))
        if index < len(self.pages):
            return self.pages[index]
        return self.pages[-1]

    def get(self, url: str, headers: dict) -> tuple[int, str]:  # pragma: no cover - unused
        return 500, "treg fetcher only POSTs"


# --- fixtures built from the pinned envelope shapes (sanitized) ---------------


def x_row(number: int = 0, **overrides) -> dict:
    """One anyapi.x.search.posts row with every pinned key present."""
    row = {
        "id": f"100{number}",
        "url": f"https://x.com/gridwatch/status/100{number}",
        "text": "Hyperscaler capex guides up again — record backlog.",
        "authorName": "Grid Watch",
        "authorUsername": "gridwatch",
        "authorId": "42",
        "authorVerified": False,
        "authorFollowers": 1200,
        "createdUtc": 1769904000,
        "likeCount": 12,
        "retweetCount": 3,
        "replyCount": 1,
        "quoteCount": 0,
        "bookmarkCount": 2,
        "viewCount": 5000,
        "isReply": False,
        "inReplyToId": None,
        "lang": "en",
        "media": [],
        "conversationId": f"100{number}",
        "authorImage": None,
    }
    row.update(overrides)
    return row


def linkedin_row(number: int = 0, **overrides) -> dict:
    """One anyapi.linkedin.search.posts row with every pinned key present."""
    row = {
        "id": f"urn:li:activity:200{number}",
        "url": f"https://www.linkedin.com/posts/gridwatch-200{number}",
        "text": "Substation transformers are sold out into next year.",
        "authorName": "Grid Watch",
        "authorUrl": "https://www.linkedin.com/in/gridwatch",
        "avatarUrl": None,
        "createdUtc": 1769904000,
        "reactionCount": 45,
        "commentCount": 6,
    }
    row.update(overrides)
    return row


def envelope(rows: list[dict], platform: str, next_cursor: str | None = None) -> str:
    """The pinned treg envelope: rows under data.items (X) / data.posts (LinkedIn)."""
    return json.dumps(
        {
            "provider": "anyapi",
            "resultId": "res_test_0001",
            "replayed": False,
            "costUsd": 0.01,
            "output": {
                "found": True,
                "data": {TREG_ROWS_KEY[platform]: rows, "nextCursor": next_cursor},
            },
        }
    )


def empty_envelope(platform: str, next_cursor: str | None = None) -> str:
    return envelope([], platform, next_cursor)


def treg_fetcher(tmp_path: Path, transport, **kwargs) -> TregFetcher:
    return TregFetcher(tmp_path, token=TOKEN, transport=transport, sleep=lambda _s: None, **kwargs)


# --- credential handling ------------------------------------------------------


def test_missing_treg_token_fails_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TREG_TOKEN", raising=False)
    monkeypatch.delenv("TREG_MAX_COST", raising=False)
    with pytest.raises(FetchError) as excinfo:
        TregFetcher(tmp_path)
    message = str(excinfo.value)
    assert "TREG_TOKEN" in message
    assert "https://treg.to" in message
    assert "Settings" in message  # where to set it in GitHub


def test_empty_treg_token_fails_loudly(tmp_path: Path):
    with pytest.raises(FetchError, match="TREG_TOKEN"):
        TregFetcher(tmp_path, token="")


def test_request_carries_x_treg_token_and_never_authorization(tmp_path: Path):
    transport = FakeTregTransport([(200, empty_envelope("x-indexed"))])
    fetcher = treg_fetcher(tmp_path, transport)
    fetcher.fetch("dc-capex", "x-indexed", ['"phrase a"', '"phrase b"'], WINDOW_END)

    url, headers, body = transport.calls[0]
    assert url == "https://treg.to/call/anyapi.x.search.posts"
    assert headers["X-Treg-Token"] == TOKEN
    assert "Authorization" not in headers
    assert body["query"] == '"phrase a" OR "phrase b" since:2026-11-11 until:2026-11-18'
    assert body["limit"] == 50
    assert "cursor" not in body


def test_treg_max_cost_env_is_forwarded_as_route_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("TREG_MAX_COST", "0.50")
    transport = FakeTregTransport([(200, empty_envelope("x-indexed"))])
    fetcher = TregFetcher(tmp_path, token=TOKEN, transport=transport, sleep=lambda _s: None)
    fetcher.fetch("dc-capex", "x-indexed", ["q"], WINDOW_END)
    assert transport.calls[0][1]["X-Treg-Route-Max-Cost"] == "0.50"


def test_no_max_cost_header_when_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TREG_MAX_COST", raising=False)
    transport = FakeTregTransport([(200, empty_envelope("x-indexed"))])
    fetcher = treg_fetcher(tmp_path, transport)
    fetcher.fetch("dc-capex", "x-indexed", ["q"], WINDOW_END)
    assert "X-Treg-Route-Max-Cost" not in transport.calls[0][1]


def test_non_200_fails_loudly(tmp_path: Path):
    transport = FakeTregTransport([(403, '{"error": "bad token"}')])
    fetcher = treg_fetcher(tmp_path, transport)
    with pytest.raises(FetchError, match="403"):
        fetcher.fetch("dc-capex", "x-indexed", ["q"], WINDOW_END)


def test_5xx_backs_off_then_succeeds(tmp_path: Path):
    transport = FakeTregTransport(
        [(503, "unavailable"), (502, "gateway"), (200, empty_envelope("x-indexed"))]
    )
    pauses: list[float] = []
    fetcher = TregFetcher(tmp_path, token=TOKEN, transport=transport, sleep=pauses.append)
    posts = fetcher.fetch("dc-capex", "x-indexed", ["q"], WINDOW_END)
    assert posts == []
    assert len(transport.calls) == 3
    assert pauses == [5.0, 10.0]  # exponential backoff, matching the repo fetch convention


def test_persistent_malformed_json_200_exhausts_budget_as_page_fetch_error(tmp_path: Path):
    transport = FakeTregTransport([(200, "<html>nope</html>")])
    pauses: list[float] = []
    fetcher = TregFetcher(tmp_path, token=TOKEN, transport=transport, sleep=pauses.append)
    with pytest.raises(PageFetchError, match=r"malformed \(not JSON\)"):
        fetcher.fetch("dc-capex", "x-indexed", ["q"], WINDOW_END)
    assert len(transport.calls) == 4  # default budget: 1 attempt + 3 retries
    assert pauses == [5.0, 10.0, 20.0]
    assert list(tmp_path.glob("*.json")) == []  # nothing malformed reached the cache


# --- malformed 200s retry and never poison the cache ---------------------------


def malformed_body() -> str:
    """Valid JSON that fails the pinned envelope contract at output.data."""
    return json.dumps(
        {
            "provider": "anyapi",
            "resultId": "res_test_0002",
            "replayed": False,
            "costUsd": 0.0,
            "output": {"found": True, "data": []},  # data must be an object
        }
    )


def test_malformed_envelope_200_retries_then_succeeds(tmp_path: Path, caplog):
    transport = FakeTregTransport(
        [(200, malformed_body()), (200, envelope([x_row(1)], "x-indexed"))]
    )
    pauses: list[float] = []
    fetcher = TregFetcher(tmp_path, token=TOKEN, transport=transport, sleep=pauses.append)

    with caplog.at_level(logging.WARNING):
        posts = fetcher.fetch("dc-capex", "x-indexed", ["q"], WINDOW_END)

    assert [p.url for p in posts] == ["https://x.com/gridwatch/status/1001"]
    assert len(transport.calls) == 2  # one glitch, one retry
    assert pauses == [5.0]  # exactly one backoff
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "malformed" in warnings[0].getMessage()
    # The malformed body never reached the cache — only the valid page is on disk.
    cache_files = list(tmp_path.glob("*.json"))
    assert len(cache_files) == 1
    assert json.loads(cache_files[0].read_text(encoding="utf-8")) == json.loads(
        envelope([x_row(1)], "x-indexed")
    )


def test_persistent_malformed_envelope_raises_page_fetch_error_with_empty_cache(tmp_path: Path):
    transport = FakeTregTransport([(200, malformed_body())])
    pauses: list[float] = []
    fetcher = TregFetcher(tmp_path, token=TOKEN, transport=transport, sleep=pauses.append)

    with pytest.raises(PageFetchError) as excinfo:
        fetcher._post_cached(  # noqa: SLF001 — unit test of the cache gate itself
            TREG_ENDPOINT["x-indexed"], "x-indexed", {"query": "q"}, ["treg", "x", "t", "q"]
        )

    # The envelope error names the endpoint and the exact path, on every attempt.
    assert "malformed at output.data" in str(excinfo.value)
    assert len(transport.calls) == 4  # default budget: 1 attempt + 3 retries
    assert pauses == [5.0, 10.0, 20.0]
    assert list(tmp_path.glob("*.json")) == []  # cache dir empty for that key


def test_persistent_malformed_cursor_page_degrades_to_shortfall(tmp_path: Path):
    transport = FakeTregTransport(
        [(200, envelope([x_row(1)], "x-indexed", "cur1")), (200, malformed_body())]
    )
    fetcher = treg_fetcher(tmp_path, transport, max_retries=0)

    posts = fetcher.fetch("dc-capex", "x-indexed", ["q"], WINDOW_END)

    # Page 1's posts are kept; the malformed page 2 exhausts the budget and
    # degrades to a shortfall instead of killing the scan.
    assert [p.url for p in posts] == ["https://x.com/gridwatch/status/1001"]
    endpoint = TREG_ENDPOINT["x-indexed"]
    assert fetcher.successful_pages[endpoint] == 1
    assert fetcher.failed_pages[endpoint] == 1
    assert fetcher.failed_queries[endpoint] == 1


def test_non_retryable_status_is_logged_before_break(tmp_path: Path, caplog):
    transport = FakeTregTransport([(403, '{"error": "bad token"}')])
    fetcher = treg_fetcher(tmp_path, transport)

    with caplog.at_level(logging.WARNING):
        with pytest.raises(FetchError) as excinfo:
            fetcher.fetch("dc-capex", "x-indexed", ["q"], WINDOW_END)

    # A dead-end 4xx is a plain FetchError (loud), not a degradable shortfall.
    assert not isinstance(excinfo.value, PageFetchError)
    assert len(transport.calls) == 1  # no retry on a non-retryable status
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("403" in r.getMessage() and "non-retryable" in r.getMessage() for r in warnings)


# --- query construction -------------------------------------------------------


def test_x_query_or_joins_variants_with_since_until_dates(tmp_path: Path):
    transport = FakeTregTransport([(200, empty_envelope("x-indexed"))])
    fetcher = treg_fetcher(tmp_path, transport)
    fetcher.fetch(
        "dc-capex",
        "x-indexed",
        ['"phrase a" (site:x.com)', '"phrase b" (site:x.com)'],
        WINDOW_END,
        window_days=7,
    )
    body = transport.calls[0][2]
    # Bare X terms are ANDed, so variants are OR-joined; the window rides the query.
    assert body["query"] == (
        '"phrase a" (site:x.com) OR "phrase b" (site:x.com) since:2026-11-11 until:2026-11-18'
    )


def test_x_window_widens_the_since_until_dates(tmp_path: Path):
    transport = FakeTregTransport([(200, empty_envelope("x-indexed"))])
    fetcher = treg_fetcher(tmp_path, transport)
    fetcher.fetch("dc-capex", "x-indexed", ["q"], WINDOW_END, window_days=30)
    assert "since:2026-10-19 until:2026-11-18" in transport.calls[0][2]["query"]


@pytest.mark.parametrize(
    ("window_days", "expected"),
    [(1, "last-day"), (7, "last-week"), (14, "last-month"), (30, "last-month")],
)
def test_linkedin_date_posted_mapping_covers_all_branches(window_days, expected):
    assert linkedin_date_posted(window_days) == expected


def test_linkedin_request_uses_dateposted_and_omits_limit(tmp_path: Path):
    transport = FakeTregTransport([(200, empty_envelope("linkedin-indexed"))])
    fetcher = treg_fetcher(tmp_path, transport)
    fetcher.fetch("dc-capex", "linkedin-indexed", ['"phrase a"', '"phrase b"'], WINDOW_END)
    _, headers, body = transport.calls[0]
    assert "anyapi.linkedin.search.posts" in transport.calls[0][0]
    assert body["datePosted"] == "last-week"  # the 7-day base window
    assert body["query"] == '"phrase a" OR "phrase b"'
    assert "limit" not in body
    assert headers["X-Treg-Token"] == TOKEN


# --- pagination ---------------------------------------------------------------


def test_cursor_from_response_n_appears_in_request_n_plus_one(tmp_path: Path):
    page_one = envelope([x_row(1)], "x-indexed", next_cursor="cur-1")
    page_two = envelope([x_row(2)], "x-indexed", next_cursor=None)
    transport = FakeTregTransport([(200, page_one), (200, page_two)])
    fetcher = treg_fetcher(tmp_path, transport)
    posts = fetcher.fetch("dc-capex", "x-indexed", ["q"], WINDOW_END)

    assert len(transport.calls) == 2
    assert "cursor" not in transport.calls[0][2]
    assert transport.calls[1][2]["cursor"] == "cur-1"
    assert [p.url for p in posts] == [
        "https://x.com/gridwatch/status/1001",
        "https://x.com/gridwatch/status/1002",
    ]


def test_pagination_stops_at_the_page_cap(tmp_path: Path):
    never_ending = envelope([x_row()], "x-indexed", next_cursor="cur-next")
    transport = FakeTregTransport([(200, never_ending)])
    fetcher = treg_fetcher(tmp_path, transport)
    posts = fetcher.fetch("dc-capex", "x-indexed", ["q"], WINDOW_END)

    assert len(transport.calls) == 3  # TREG_MAX_PAGES
    for index in range(1, 3):
        assert transport.calls[index][2]["cursor"] == "cur-next"
    # Every page returns the same URL: dedupe keeps exactly one post.
    assert len(posts) == 1


def test_cache_resume_second_run_fetches_nothing(tmp_path: Path):
    transport = FakeTregTransport([(200, envelope([x_row(1)], "x-indexed"))])
    fetcher = treg_fetcher(tmp_path, transport)

    first = fetcher.fetch("dc-capex", "x-indexed", ["q"], WINDOW_END)
    assert len(first) == 1
    assert len(transport.calls) == 1

    second = fetcher.fetch("dc-capex", "x-indexed", ["q"], WINDOW_END)
    assert second == first
    assert len(transport.calls) == 1  # served entirely from cache

    # A widened window re-fetches — the rolling window is not served stale.
    fetcher.fetch("dc-capex", "x-indexed", ["q"], WINDOW_END, window_days=30)
    assert len(transport.calls) == 2


# --- row mapping --------------------------------------------------------------


def test_x_rows_map_to_the_indexed_post_shape(tmp_path: Path):
    transport = FakeTregTransport([(200, envelope([x_row(7)], "x-indexed"))])
    fetcher = treg_fetcher(tmp_path, transport)
    posts = fetcher.fetch("dc-capex", "x-indexed", ["q"], WINDOW_END)

    assert len(posts) == 1
    post = posts[0]
    assert post.platform == "x-indexed"
    assert post.author == "gridwatch"
    assert post.url == "https://x.com/gridwatch/status/1007"
    assert post.post_date == "2026-02-01"  # from createdUtc 1769904000
    assert "Hyperscaler capex" in post.snippet
    # Engagement proxy: likes + retweets + views (scorer still flags it as proxy).
    assert post.engagement == pytest.approx(12 + 3 + 5000)


def test_linkedin_rows_map_to_the_indexed_post_shape(tmp_path: Path):
    transport = FakeTregTransport([(200, envelope([linkedin_row(3)], "linkedin-indexed"))])
    fetcher = treg_fetcher(tmp_path, transport)
    posts = fetcher.fetch("dc-capex", "linkedin-indexed", ["q"], WINDOW_END)

    assert len(posts) == 1
    post = posts[0]
    assert post.platform == "linkedin-indexed"
    assert post.author == "Grid Watch"
    assert post.url == "https://www.linkedin.com/posts/gridwatch-2003"
    assert post.engagement == pytest.approx(45 + 6)


def test_unknown_platform_rejected(tmp_path: Path):
    fetcher = TregFetcher(tmp_path, token=TOKEN, sleep=lambda _s: None)
    with pytest.raises(FetchError, match="unknown indexed platform"):
        fetcher.fetch("dc-capex", "reddit", ["q"], WINDOW_END)


# --- envelope hardening -------------------------------------------------------


def malformed_cases(platform: str) -> dict[str, str]:
    """Each malformed envelope paired with the path fragment the error must name."""
    rows_key = TREG_ROWS_KEY[platform]
    good = envelope([x_row() if platform == "x-indexed" else linkedin_row()], platform)
    no_output = json.loads(good)
    del no_output["output"]
    no_found = json.loads(good)
    del no_found["output"]["found"]
    found_not_bool = json.loads(good)
    found_not_bool["output"]["found"] = "yes"
    no_data = json.loads(good)
    del no_data["output"]["data"]
    data_not_dict = json.loads(good)
    data_not_dict["output"]["data"] = []
    rows_not_list = json.loads(good)
    rows_not_list["output"]["data"][rows_key] = {"oops": 1}
    row_not_object = json.loads(good)
    row_not_object["output"]["data"][rows_key] = ["not-an-object"]
    row_missing_keys = json.loads(good)
    del row_missing_keys["output"]["data"][rows_key][0]["createdUtc"]
    row_missing_counts = json.loads(good)
    del row_missing_counts["output"]["data"][rows_key][0]["text"]
    cursor_not_string = json.loads(good)
    cursor_not_string["output"]["data"]["nextCursor"] = 17
    return {
        "no_output": (json.dumps(no_output), "output"),
        "no_found": (json.dumps(no_found), "output.found"),
        "found_not_bool": (json.dumps(found_not_bool), "output.found"),
        "no_data": (json.dumps(no_data), "output.data"),
        "data_not_dict": (json.dumps(data_not_dict), "output.data"),
        "rows_not_list": (json.dumps(rows_not_list), f"output.data.{rows_key}"),
        "row_not_object": (json.dumps(row_not_object), f"output.data.{rows_key}[0]"),
        "row_missing_created": (json.dumps(row_missing_keys), "createdUtc"),
        "row_missing_text": (json.dumps(row_missing_counts), "text"),
        "cursor_not_string": (json.dumps(cursor_not_string), "nextCursor"),
    }


@pytest.mark.parametrize("platform", ["x-indexed", "linkedin-indexed"])
@pytest.mark.parametrize("case", list(malformed_cases("x-indexed")))
def test_malformed_envelopes_fail_loudly_naming_endpoint_and_path(
    tmp_path: Path, platform: str, case: str
):
    body, path = malformed_cases(platform)[case]
    transport = FakeTregTransport([(200, body)])
    fetcher = treg_fetcher(tmp_path, transport)
    with pytest.raises(FetchError) as excinfo:
        fetcher.fetch("dc-capex", platform, ["q"], WINDOW_END)
    message = str(excinfo.value)
    assert "treg" in message
    assert "malformed" in message
    assert path in message


# --- cache-hit validation and soft-empty pages (regression: 2026-11-19) ---------


def poisoned_envelope() -> str:
    """The malformed shape cached by the pre-#39 run: output.data is null."""
    return json.dumps(
        {
            "output": {"found": False, "data": None, "reason": "not_found"},
        }
    )


def first_page_key_parts(platform: str = "x-indexed") -> list[str]:
    """The cache-key parts fetch() builds for page 1 of the default window."""
    window_start = WINDOW_END - timedelta(days=7)
    query = "q" if platform != "x-indexed" else "q since:2026-11-11 until:2026-11-18"
    return [
        "treg",
        platform,
        "dc-capex",
        query,
        "7",
        window_start.isoformat(),
        WINDOW_END.isoformat(),
        "1",
    ]


def test_cache_hit_with_malformed_envelope_is_discarded_and_refetched(tmp_path: Path, caplog):
    path = _cache_path(tmp_path, first_page_key_parts())
    path.write_text(poisoned_envelope(), encoding="utf-8")
    transport = FakeTregTransport([(200, envelope([x_row(1)], "x-indexed"))])
    fetcher = treg_fetcher(tmp_path, transport)

    with caplog.at_level(logging.WARNING):
        posts = fetcher.fetch("dc-capex", "x-indexed", ["q"], WINDOW_END)

    assert [p.url for p in posts] == ["https://x.com/gridwatch/status/1001"]
    assert len(transport.calls) == 1  # the poisoned file did not satisfy the page
    assert any("discarding invalid cached envelope" in r.getMessage() for r in caplog.records)
    # The poisoned file was replaced by the valid envelope from the network.
    assert json.loads(path.read_text(encoding="utf-8")) == json.loads(
        envelope([x_row(1)], "x-indexed")
    )


def test_cache_hit_with_valid_envelope_serves_without_network(tmp_path: Path):
    transport = FakeTregTransport([(200, envelope([x_row(1)], "x-indexed"))])
    fetcher = treg_fetcher(tmp_path, transport)

    first = fetcher.fetch("dc-capex", "x-indexed", ["q"], WINDOW_END)
    assert len(transport.calls) == 1

    second = fetcher.fetch("dc-capex", "x-indexed", ["q"], WINDOW_END)
    assert second == first
    assert len(transport.calls) == 1  # valid cache hit, zero network calls


def test_zero_row_fresh_fetch_returns_but_is_not_cached(tmp_path: Path):
    transport = FakeTregTransport([(200, envelope([], "x-indexed"))])
    fetcher = treg_fetcher(tmp_path, transport)

    posts = fetcher.fetch("dc-capex", "x-indexed", ["q"], WINDOW_END)

    assert posts == []  # an honest empty page still returns normally
    assert list(tmp_path.glob("*.json")) == []  # ... but never pins itself
    fetcher.fetch("dc-capex", "x-indexed", ["q"], WINDOW_END)
    assert len(transport.calls) == 2  # the next run refetches the soft-empty page


def test_fresh_fetch_with_rows_is_cached(tmp_path: Path):
    transport = FakeTregTransport([(200, envelope([x_row(1)], "x-indexed"))])
    fetcher = treg_fetcher(tmp_path, transport)

    fetcher.fetch("dc-capex", "x-indexed", ["q"], WINDOW_END)

    cache_files = list(tmp_path.glob("*.json"))
    assert len(cache_files) == 1
    assert json.loads(cache_files[0].read_text(encoding="utf-8")) == json.loads(
        envelope([x_row(1)], "x-indexed")
    )


def test_cache_hit_validation_failure_then_persistent_network_failure_raises_page_fetch_error(
    tmp_path: Path,
):
    path = _cache_path(tmp_path, first_page_key_parts())
    path.write_text(poisoned_envelope(), encoding="utf-8")
    transport = AlwaysTransportErrorTransport()
    fetcher = TregFetcher(tmp_path, token=TOKEN, transport=transport, max_retries=2)

    with pytest.raises(PageFetchError):
        fetcher.fetch("dc-capex", "x-indexed", ["q"], WINDOW_END)

    assert not path.exists()  # the poisoned file did not survive the run


def test_found_false_is_an_honest_empty_result(tmp_path: Path):

    payload = envelope([], "x-indexed")
    parsed = json.loads(payload)
    parsed["output"]["found"] = False
    transport = FakeTregTransport([(200, json.dumps(parsed))])
    fetcher = treg_fetcher(tmp_path, transport)
    assert fetcher.fetch("dc-capex", "x-indexed", ["q"], WINDOW_END) == []


# --- query chunking under the not_found threshold (regression: 2026-11-19) ------
# The gateway deterministically answers found:false/not_found for OR-joined
# queries above ~320-333 chars (verified live: 319 ch found:true, 333 ch
# not_found), which killed every LinkedIn phase on long variant lists. The
# fetcher now packs variants into chunks under TREG_QUERY_CHUNK_MAX_CHARS.


def test_chunk_queries_greedy_boundary():
    """A chunk closes the moment adding the next variant would cross the cap."""
    a, b, c = "a" * 10, "b" * 10, "c" * 5
    # a OR b = 23 chars > 20 cap -> a alone; b OR c = 16 chars fits.
    assert chunk_queries([a, b, c], max_chars=20) == [[a], [b, c]]


def test_chunk_queries_all_fits_is_unchanged():
    """When the full OR-join fits the cap there is exactly one chunk — the old path."""
    variants = ['"phrase a"', '"phrase b"', '"phrase c"']
    assert chunk_queries(variants) == [variants]


def test_chunk_queries_oversized_single_variant_is_sent_alone():
    """A variant longer than the cap is never split mid-variant."""
    big = "x" * 500
    assert chunk_queries([big, "small"], max_chars=TREG_QUERY_CHUNK_MAX_CHARS) == [
        [big],
        ["small"],
    ]


def test_chunk_queries_empty_input_yields_no_chunks():
    assert chunk_queries([]) == []


def test_long_variant_list_posts_only_queries_under_the_cap(tmp_path: Path):
    """Every POSTed OR-join stays under the cap and no variant is lost or split."""
    variants = [f'"variant phrase {i:02d} datacenter capex"' for i in range(12)]
    transport = FakeTregTransport([(200, empty_envelope("linkedin-indexed"))])
    fetcher = treg_fetcher(tmp_path, transport)

    fetcher.fetch("dc-capex", "linkedin-indexed", variants, WINDOW_END)

    assert len(transport.calls) > 1  # the full OR-join could not have fit one query
    for _url, _headers, body in transport.calls:
        assert len(body["query"]) <= TREG_QUERY_CHUNK_MAX_CHARS
    # Concatenating the chunk queries reproduces the full OR-join exactly:
    # order preserved, no variant split or dropped.
    full_join = " OR ".join(body["query"] for _url, _headers, body in transport.calls)
    assert full_join == " OR ".join(variants)


def test_chunked_posts_merge_dedup_by_url_first_seen_order(tmp_path: Path):
    """Rows repeated across chunks dedupe by URL, keeping first-seen order."""
    variants = ["a" * 200, "b" * 200, "c" * 200]  # three chunks under the cap
    pages = [
        (200, envelope([x_row(1)], "x-indexed")),
        (200, envelope([x_row(1), x_row(2)], "x-indexed")),  # row 1 repeats
        (200, envelope([x_row(3)], "x-indexed")),
    ]
    transport = FakeTregTransport(pages)
    fetcher = treg_fetcher(tmp_path, transport)

    posts = fetcher.fetch("dc-capex", "x-indexed", variants, WINDOW_END)

    assert [p.url for p in posts] == [
        "https://x.com/gridwatch/status/1001",
        "https://x.com/gridwatch/status/1002",
        "https://x.com/gridwatch/status/1003",
    ]


def test_shortfall_accounting_sums_across_chunks(tmp_path: Path):
    """A mid-run chunk failure records the shortfall and later chunks still proceed."""
    variants = ["a" * 200, "b" * 200, "c" * 200]
    pages = [
        (200, envelope([x_row(1)], "x-indexed")),  # chunk 1 answers
        (500, "boom"),  # chunk 2's first page fails persistently (max_retries=0)
        (200, envelope([x_row(3)], "x-indexed")),  # chunk 3 still proceeds
    ]
    transport = FakeTregTransport(pages)
    fetcher = treg_fetcher(tmp_path, transport, max_retries=0)

    posts = fetcher.fetch("dc-capex", "x-indexed", variants, WINDOW_END)

    assert [p.url for p in posts] == [
        "https://x.com/gridwatch/status/1001",
        "https://x.com/gridwatch/status/1003",
    ]
    endpoint = TREG_ENDPOINT["x-indexed"]
    assert fetcher.successful_pages[endpoint] == 2
    assert fetcher.failed_pages[endpoint] == 1
    assert fetcher.failed_queries[endpoint] == 1


def test_malformed_chunk_page_degrades_and_remaining_chunks_proceed(tmp_path: Path):
    """Envelope validation is unchanged per chunk: a malformed page is a shortfall."""
    variants = ["a" * 200, "b" * 200, "c" * 200]
    pages = [
        (200, envelope([x_row(1)], "x-indexed")),
        (200, malformed_body()),  # chunk 2's first page: malformed, exhausts budget
        (200, envelope([x_row(3)], "x-indexed")),
    ]
    transport = FakeTregTransport(pages)
    fetcher = treg_fetcher(tmp_path, transport, max_retries=0)

    posts = fetcher.fetch("dc-capex", "x-indexed", variants, WINDOW_END)

    assert [p.url for p in posts] == [
        "https://x.com/gridwatch/status/1001",
        "https://x.com/gridwatch/status/1003",
    ]
    endpoint = TREG_ENDPOINT["x-indexed"]
    assert fetcher.successful_pages[endpoint] == 2
    assert fetcher.failed_pages[endpoint] == 1
    assert fetcher.failed_queries[endpoint] == 1


def test_zero_row_chunk_is_not_cached_but_valid_chunk_is(tmp_path: Path):
    """PR #40 semantics hold per chunk: zero-row pages return but never pin the cache."""
    variants = ["a" * 200, "b" * 200]  # two chunks
    pages = [
        (200, envelope([x_row(1)], "x-indexed")),  # chunk 1: rows, cached
        (200, envelope([], "x-indexed")),  # chunk 2: zero rows, not cached
    ]
    transport = FakeTregTransport(pages)
    fetcher = treg_fetcher(tmp_path, transport)

    posts = fetcher.fetch("dc-capex", "x-indexed", variants, WINDOW_END)

    assert [p.url for p in posts] == ["https://x.com/gridwatch/status/1001"]
    cache_files = list(tmp_path.glob("*.json"))
    assert len(cache_files) == 1  # only the chunk that returned rows
    assert json.loads(cache_files[0].read_text(encoding="utf-8")) == json.loads(
        envelope([x_row(1)], "x-indexed")
    )


# --- run_scan wiring ----------------------------------------------------------


class FakeReddit:
    """Stands in for RedditTregFetcher so run_scan never builds a real gateway fetcher."""

    def fetch(self, topic_id, queries, window_end, *, t="week"):  # noqa: ANN001
        return []


class FakeTreg:
    """Stands in for TregFetcher; records what the controller asked for."""

    def __init__(self):
        self.requested: list[tuple[str, str, list[str], int]] = []

    def fetch(self, topic_id, platform, queries, window_end, *, window_days=7):  # noqa: ANN001
        self.requested.append((topic_id, platform, list(queries), window_days))
        return []


def test_run_scan_routes_indexed_platforms_through_treg(tmp_path: Path):
    fake = FakeTreg()
    options = ScanOptions(
        platforms=("x-indexed", "linkedin-indexed"),
        topic_ids=("dc-capex",),
        as_of="2026-11-18T12:00:00Z",
        cache_dir=tmp_path / "cache",
        out_dir=tmp_path / "out",
    )
    _, payload = run_scan(load_matrix(), options, reddit=FakeReddit(), treg=fake)

    # One fetch per platform per ladder rung the controller walks; the variants
    # arrive expanded and are OR-joined inside the fetcher.
    assert {platform for _, platform, _, _ in fake.requested} == {
        "x-indexed",
        "linkedin-indexed",
    }
    assert all(len(queries) > 1 for _, _, queries, _ in fake.requested)
    for block in payload["topics"]:
        assert block["post_count"] == 0  # honest empty rows


# --- one bad page degrades, it does not abort ----------------------------------


def test_persistent_500_on_one_page_degrades_to_shortfall(tmp_path: Path):
    transport = FakeTregTransport([(200, envelope([x_row(1)], "x-indexed", "cur1")), (500, "boom")])
    fetcher = treg_fetcher(tmp_path, transport, max_retries=0)

    posts = fetcher.fetch("dc-capex", "x-indexed", ["q"], WINDOW_END)

    # Page 1's posts are kept; the failed page 2 stops only this pagination.
    assert [p.url for p in posts] == ["https://x.com/gridwatch/status/1001"]
    endpoint = TREG_ENDPOINT["x-indexed"]
    assert fetcher.successful_pages[endpoint] == 1
    assert fetcher.failed_pages[endpoint] == 1
    assert fetcher.failed_queries[endpoint] == 1


def test_run_scan_aborts_when_every_x_page_fails(tmp_path: Path):
    transport = FakeTregTransport([(500, "down")])
    options = ScanOptions(
        platforms=("x-indexed",),
        topic_ids=("dc-capex",),
        as_of="2026-11-18T12:00:00Z",
        cache_dir=tmp_path / "cache",
        out_dir=tmp_path / "out",
    )
    fetcher = treg_fetcher(tmp_path, transport, max_retries=0)

    with pytest.raises(FetchError, match="treg call failed"):
        run_scan(load_matrix(), options, reddit=FakeReddit(), treg=fetcher)


class CursorPagesFailTregTransport:
    """First pages answer with a row + cursor; every cursor page 500s."""

    def __init__(self, platform: str) -> None:
        self.platform = platform
        self.calls: list[str] = []
        self.pages_served = 0

    def post(self, url: str, headers: dict, body: str, content_type: str) -> tuple[int, str]:
        self.calls.append(url)
        if "cursor" in json.loads(body):
            return 500, "boom"
        self.pages_served += 1
        return 200, envelope([x_row(self.pages_served)], self.platform, "cur_next")

    def get(self, url: str, headers: dict) -> tuple[int, str]:  # pragma: no cover - unused
        return 400, "treg fetcher only POSTs"


def test_run_scan_completes_when_x_cursor_pages_fail(tmp_path: Path):
    transport = CursorPagesFailTregTransport("x-indexed")
    fetcher = treg_fetcher(tmp_path, transport, max_retries=0)
    options = ScanOptions(
        platforms=("x-indexed",),
        topic_ids=("dc-capex",),
        as_of="2026-11-18T12:00:00Z",
        cache_dir=tmp_path / "cache",
        out_dir=tmp_path / "out",
    )

    run_path, payload = run_scan(load_matrix(), options, reddit=FakeReddit(), treg=fetcher)

    coverage = payload["coverage"]["x-indexed"]
    assert coverage["kept"] > 0  # every first page answered and is retained
    assert coverage["failed_pages"] >= 1
    assert coverage["failed_queries"] >= 1
    assert coverage["failed_endpoints"] == {TREG_ENDPOINT["x-indexed"]: coverage["failed_pages"]}
    assert run_path.is_file()
