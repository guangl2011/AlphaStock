"""treg.to gateway fetchers against the pinned envelope — no test touches the network.

Envelope and row shapes are the live-pinned contract verified 2026-11-18:
POST https://treg.to/call/{endpoint_id} with X-Treg-Token answers
{provider, resultId, replayed, costUsd, output: {found, data}} where data
carries the rows under ``items`` (X) / ``posts`` (LinkedIn) plus nextCursor.
All fixture authors, URLs, and ids are synthetic.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from pipelines.sentiment.fetchers import (
    TREG_ROWS_KEY,
    FetchError,
    TregFetcher,
    linkedin_date_posted,
)
from pipelines.sentiment.run_scan import ScanOptions, run_scan

WINDOW_END = date(2026, 11, 18)
TOKEN = "test-treg-token"


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


def test_non_json_success_body_fails_loudly(tmp_path: Path):
    transport = FakeTregTransport([(200, "<html>nope</html>")])
    fetcher = treg_fetcher(tmp_path, transport)
    with pytest.raises(FetchError, match="malformed"):
        fetcher.fetch("dc-capex", "x-indexed", ["q"], WINDOW_END)


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
        '"phrase a" (site:x.com) OR "phrase b" (site:x.com) '
        "since:2026-11-11 until:2026-11-18"
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


def test_found_false_is_an_honest_empty_result(tmp_path: Path):
    payload = envelope([], "x-indexed")
    parsed = json.loads(payload)
    parsed["output"]["found"] = False
    transport = FakeTregTransport([(200, json.dumps(parsed))])
    fetcher = treg_fetcher(tmp_path, transport)
    assert fetcher.fetch("dc-capex", "x-indexed", ["q"], WINDOW_END) == []


# --- run_scan wiring ----------------------------------------------------------


class FakeReddit:
    """Stands in for RedditFetcher so run_scan never builds a real OAuth fetcher."""

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
    from pipelines.sentiment.matrix import load_matrix

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
