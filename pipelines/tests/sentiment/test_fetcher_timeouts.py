"""Read/connect-timeout handling in the treg fetchers — no test touches the network.

Regression for the live 2026-11-19 scans: a socket/SSL read timeout escaped the
transport and the retry loops, killing run_scan with a raw TimeoutError. The
transport now surfaces timeouts as TransportTimeout and the retry loops absorb
them within the existing max-retries/backoff budget before raising FetchError.
All transport stubs raise in-process; nothing here dials treg.to.
"""

import json
import logging
import urllib.error
from datetime import date
from pathlib import Path

import pytest

from pipelines.sentiment.fetchers import (
    FetchError,
    PageFetchError,
    RedditTregFetcher,
    TransportTimeout,
    TregFetcher,
    UrllibTransport,
)

WINDOW_END = date(2026, 11, 19)
TOKEN = "test-treg-token"
TIMEOUT_TEXT = "The read operation timed out"


class TimeoutThenTransport:
    """Raises TimeoutError on the first call, then answers from ``pages`` in order."""

    def __init__(self, pages: list[tuple[int, str]]):
        self.pages = pages
        self.calls = 0

    def _next(self) -> tuple[int, str]:
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError(TIMEOUT_TEXT)
        return self.pages[min(self.calls - 2, len(self.pages) - 1)]

    def get(self, url: str, headers: dict) -> tuple[int, str]:
        return self._next()

    def post(self, url: str, headers: dict, body: str, content_type: str) -> tuple[int, str]:
        return self._next()


class AlwaysTimeoutTransport:
    """Raises TimeoutError on every call."""

    def __init__(self, method: str):
        self.method = method
        self.calls = 0

    def get(self, url: str, headers: dict) -> tuple[int, str]:
        self.calls += 1
        raise TimeoutError(TIMEOUT_TEXT)

    def post(self, url: str, headers: dict, body: str, content_type: str) -> tuple[int, str]:
        self.calls += 1
        raise TimeoutError(TIMEOUT_TEXT)


class TransportTimeoutThenTransport:
    """Raises TransportTimeout (as the real UrllibTransport does) on the first
    call, then answers from ``pages`` in order — the seam PR #35 never tested."""

    def __init__(self, pages: list[tuple[int, str]]):
        self.pages = pages
        self.calls = 0

    def _next(self) -> tuple[int, str]:
        self.calls += 1
        if self.calls == 1:
            raise TransportTimeout(TIMEOUT_TEXT)
        return self.pages[min(self.calls - 2, len(self.pages) - 1)]

    def get(self, url: str, headers: dict) -> tuple[int, str]:
        return self._next()

    def post(self, url: str, headers: dict, body: str, content_type: str) -> tuple[int, str]:
        return self._next()


class AlwaysTransportTimeoutTransport:
    """Raises TransportTimeout (never a raw TimeoutError) on every call."""

    def __init__(self, method: str):
        self.method = method
        self.calls = 0

    def get(self, url: str, headers: dict) -> tuple[int, str]:
        self.calls += 1
        raise TransportTimeout(TIMEOUT_TEXT)

    def post(self, url: str, headers: dict, body: str, content_type: str) -> tuple[int, str]:
        self.calls += 1
        raise TransportTimeout(TIMEOUT_TEXT)


class NonTimeoutUrlErrorTransport:
    """Raises a bare URLError (DNS/refused shape) on every call — not retryable."""

    def __init__(self, method: str):
        self.method = method
        self.calls = 0

    def get(self, url: str, headers: dict) -> tuple[int, str]:
        self.calls += 1
        raise urllib.error.URLError("connection refused")

    def post(self, url: str, headers: dict, body: str, content_type: str) -> tuple[int, str]:
        self.calls += 1
        raise urllib.error.URLError("connection refused")


class SleepRecorder:
    def __init__(self):
        self.sleeps: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def reddit_page() -> str:
    row = {
        "permalink": "/r/energy/comments/abc/test_post/",
        "author": "gridwatch",
        "title": "Transformer backlog",
        "selftext": "",
        "created_utc": 1769904000,
        "score": 10,
        "num_comments": 3,
    }
    return json.dumps({"success": True, "posts": [row], "after": None})


def x_envelope() -> str:
    row = {
        "url": "https://x.com/gridwatch/status/1000",
        "text": "Transformer backlog",
        "authorUsername": "gridwatch",
        "createdUtc": 1769904000,
        "likeCount": 12,
        "retweetCount": 3,
        "viewCount": 5000,
    }
    return json.dumps(
        {
            "provider": "anyapi",
            "resultId": "res_test_0001",
            "replayed": False,
            "costUsd": 0.01,
            "output": {"found": True, "data": {"items": [row], "nextCursor": None}},
        }
    )


def reddit_fetcher(tmp_path: Path, transport, sleep) -> RedditTregFetcher:
    return RedditTregFetcher(
        tmp_path, token=TOKEN, transport=transport, sleep=sleep, backoff_base_seconds=5.0
    )


def x_fetcher(tmp_path: Path, transport, sleep) -> TregFetcher:
    return TregFetcher(
        tmp_path,
        token=TOKEN,
        transport=transport,
        sleep=sleep,
        backoff_base_seconds=5.0,
    )


# --- GET path (Reddit site-wide search) ---------------------------------------


def test_get_transient_timeout_is_retried_within_existing_budget(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    sleep = SleepRecorder()
    transport = TimeoutThenTransport([(200, reddit_page())])
    with caplog.at_level(logging.WARNING, logger="pipelines.sentiment.fetchers"):
        posts = reddit_fetcher(tmp_path, transport, sleep).fetch(
            "topic-a", [("energy", "grid")], WINDOW_END
        )
    assert len(posts) == 1
    assert transport.calls == 2  # one timeout, one success
    assert sleep.sleeps == [5.0]  # one backoff sleep, existing budget
    warnings = [r for r in caplog.records if "timed out" in r.getMessage()]
    assert len(warnings) == 1


def test_get_persistent_timeout_raises_fetch_error_not_raw_timeout(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    sleep = SleepRecorder()
    transport = AlwaysTimeoutTransport("get")
    with caplog.at_level(logging.WARNING, logger="pipelines.sentiment.fetchers"):
        with pytest.raises(FetchError) as excinfo:
            reddit_fetcher(tmp_path, transport, sleep).fetch(
                "topic-a", [("energy", "grid")], WINDOW_END
            )
    message = str(excinfo.value)
    assert "reddit" in message  # the platform
    assert "https://treg.to/call" in message  # the URL prefix
    assert TIMEOUT_TEXT in message  # the underlying reason, not swallowed
    assert transport.calls == 4  # max_retries + 1 attempts, same budget
    assert sleep.sleeps == [5.0, 10.0, 20.0]  # exponential backoff between attempts
    assert len([r for r in caplog.records if "timed out" in r.getMessage()]) == 4
    # Failed attempts are never cached.
    assert list(tmp_path.glob("*.json")) == []


# --- POST gateway path (indexed X) ---------------------------------------------


def test_post_transient_timeout_is_retried_within_existing_budget(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    sleep = SleepRecorder()
    transport = TimeoutThenTransport([(200, x_envelope())])
    with caplog.at_level(logging.WARNING, logger="pipelines.sentiment.fetchers"):
        posts = x_fetcher(tmp_path, transport, sleep).fetch(
            "topic-a", "x-indexed", ["grid"], WINDOW_END
        )
    assert len(posts) == 1
    assert transport.calls == 2
    assert sleep.sleeps == [5.0]
    assert len([r for r in caplog.records if "timed out" in r.getMessage()]) == 1


def test_post_persistent_timeout_raises_fetch_error_not_raw_timeout(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    sleep = SleepRecorder()
    transport = AlwaysTimeoutTransport("post")
    with caplog.at_level(logging.WARNING, logger="pipelines.sentiment.fetchers"):
        with pytest.raises(FetchError) as excinfo:
            x_fetcher(tmp_path, transport, sleep).fetch(
                "topic-a", "x-indexed", ["grid"], WINDOW_END
            )
    message = str(excinfo.value)
    assert "treg" in message  # the platform
    assert "anyapi.x.search.posts" in message  # the gateway endpoint
    assert TIMEOUT_TEXT in message
    assert transport.calls == 4
    assert sleep.sleeps == [5.0, 10.0, 20.0]
    assert list(tmp_path.glob("*.json")) == []


# --- integration seam: transport raising TransportTimeout through the loops ----


def test_get_transporttimeout_transient_is_retried_within_existing_budget(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """The live-crash seam: UrllibTransport's own TransportTimeout must retry."""
    sleep = SleepRecorder()
    transport = TransportTimeoutThenTransport([(200, reddit_page())])
    with caplog.at_level(logging.WARNING, logger="pipelines.sentiment.fetchers"):
        posts = reddit_fetcher(tmp_path, transport, sleep).fetch(
            "topic-a", [("energy", "grid")], WINDOW_END
        )
    assert len(posts) == 1
    assert transport.calls == 2
    assert sleep.sleeps == [5.0]
    assert len([r for r in caplog.records if "timed out" in r.getMessage()]) == 1


def test_get_transporttimeout_persistent_raises_page_fetch_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    sleep = SleepRecorder()
    transport = AlwaysTransportTimeoutTransport("get")
    with caplog.at_level(logging.WARNING, logger="pipelines.sentiment.fetchers"):
        with pytest.raises(PageFetchError) as excinfo:
            reddit_fetcher(tmp_path, transport, sleep).fetch(
                "topic-a", [("energy", "grid")], WINDOW_END
            )
    assert TIMEOUT_TEXT in str(excinfo.value)
    assert transport.calls == 4
    assert sleep.sleeps == [5.0, 10.0, 20.0]
    assert len([r for r in caplog.records if "timed out" in r.getMessage()]) == 4
    assert list(tmp_path.glob("*.json")) == []


def test_post_transporttimeout_transient_is_retried_within_existing_budget(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    sleep = SleepRecorder()
    transport = TransportTimeoutThenTransport([(200, x_envelope())])
    with caplog.at_level(logging.WARNING, logger="pipelines.sentiment.fetchers"):
        posts = x_fetcher(tmp_path, transport, sleep).fetch(
            "topic-a", "x-indexed", ["grid"], WINDOW_END
        )
    assert len(posts) == 1
    assert transport.calls == 2
    assert sleep.sleeps == [5.0]
    assert len([r for r in caplog.records if "timed out" in r.getMessage()]) == 1


def test_post_transporttimeout_persistent_raises_page_fetch_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    sleep = SleepRecorder()
    transport = AlwaysTransportTimeoutTransport("post")
    with caplog.at_level(logging.WARNING, logger="pipelines.sentiment.fetchers"):
        with pytest.raises(PageFetchError) as excinfo:
            x_fetcher(tmp_path, transport, sleep).fetch(
                "topic-a", "x-indexed", ["grid"], WINDOW_END
            )
    assert TIMEOUT_TEXT in str(excinfo.value)
    assert transport.calls == 4
    assert sleep.sleeps == [5.0, 10.0, 20.0]
    assert list(tmp_path.glob("*.json")) == []


def test_non_timeout_urlerror_through_fetch_loop_reraises_immediately(tmp_path: Path):
    """A non-timeout URLError stays non-retryable: re-raised on the first attempt."""
    sleep = SleepRecorder()
    transport = NonTimeoutUrlErrorTransport("get")
    with pytest.raises(urllib.error.URLError):
        reddit_fetcher(tmp_path, transport, sleep).fetch(
            "topic-a", [("energy", "grid")], WINDOW_END
        )
    assert transport.calls == 1
    assert sleep.sleeps == []


# --- UrllibTransport conversion (patched urlopen, no network) ------------------


def test_urllib_transport_converts_read_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def raise_timeout(request, timeout):
        raise TimeoutError(TIMEOUT_TEXT)

    monkeypatch.setattr(urllib.request, "urlopen", raise_timeout)
    with pytest.raises(TransportTimeout) as excinfo:
        UrllibTransport().get("https://treg.to/call/x", {})
    assert TIMEOUT_TEXT in str(excinfo.value)


def test_urllib_transport_converts_connect_timeout_wrapped_in_urlerror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def raise_urlerror(request, timeout):
        raise urllib.error.URLError(TimeoutError(TIMEOUT_TEXT))

    monkeypatch.setattr(urllib.request, "urlopen", raise_urlerror)
    with pytest.raises(TransportTimeout):
        UrllibTransport().post("https://treg.to/call/x", {}, "{}", "application/json")


def test_urllib_transport_keeps_non_timeout_urlerror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """DNS/refused failures are not retries — the original error still escapes."""

    def raise_urlerror(request, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", raise_urlerror)
    with pytest.raises(urllib.error.URLError):
        UrllibTransport().get("https://treg.to/call/x", {})
