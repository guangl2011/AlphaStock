"""Network-failure handling in the treg fetchers — no test touches the network.

Regression for the live 2026-11-19 scans: a socket/SSL read timeout escaped the
transport and the retry loops, killing run_scan with a raw TimeoutError. The
transport now surfaces timeouts as TransportTimeout and the retry loops absorb
them within the existing max-retries/backoff budget before raising FetchError.
PR #38 closes the sibling hole: body-read socket errors (ConnectionResetError
and other OSError subclasses urllib never wraps in URLError) are classified by
the transport as TransportError, and the loops retry them under the same
budget before degrading the page to PageFetchError. All transport stubs raise
in-process; nothing here dials treg.to.
"""

import json
import logging
import urllib.error
from datetime import date
from pathlib import Path

import pytest

from pipelines.sentiment.fetchers import (
    REDDIT_TREG_ENDPOINT,
    TREG_ENDPOINT,
    FetchError,
    PageFetchError,
    RedditTregFetcher,
    TransportError,
    TransportTimeout,
    TregFetcher,
    UrllibTransport,
)

WINDOW_END = date(2026, 11, 19)
TOKEN = "test-treg-token"
TIMEOUT_TEXT = "The read operation timed out"
RESET_TEXT = "[Errno 104] Connection reset by peer"
CERT_ERROR_TEXT = "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"


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


class AlwaysUrlErrorTransport:
    """Raises a non-timeout URLError (certificate-failure shape) on every call.

    Under the closed transport contract this is a network-level failure like
    any other: retried within the existing budget, then degraded — the
    behavior change from PR #38 (previously it aborted on the first attempt).
    """

    def __init__(self, method: str):
        self.method = method
        self.calls = 0

    def get(self, url: str, headers: dict) -> tuple[int, str]:
        self.calls += 1
        raise urllib.error.URLError(CERT_ERROR_TEXT)

    def post(self, url: str, headers: dict, body: str, content_type: str) -> tuple[int, str]:
        self.calls += 1
        raise urllib.error.URLError(CERT_ERROR_TEXT)


class ConnectionResetThenTransport:
    """Raises ConnectionResetError (the live-crash shape) on the first call,
    then answers from ``pages`` in order."""

    def __init__(self, pages: list[tuple[int, str]]):
        self.pages = pages
        self.calls = 0

    def _next(self) -> tuple[int, str]:
        self.calls += 1
        if self.calls == 1:
            raise ConnectionResetError(104, "Connection reset by peer")
        return self.pages[min(self.calls - 2, len(self.pages) - 1)]

    def get(self, url: str, headers: dict) -> tuple[int, str]:
        return self._next()

    def post(self, url: str, headers: dict, body: str, content_type: str) -> tuple[int, str]:
        return self._next()


class AlwaysResetTransport:
    """Raises ConnectionResetError on every call."""

    def __init__(self, method: str):
        self.method = method
        self.calls = 0

    def get(self, url: str, headers: dict) -> tuple[int, str]:
        self.calls += 1
        raise ConnectionResetError(104, "Connection reset by peer")

    def post(self, url: str, headers: dict, body: str, content_type: str) -> tuple[int, str]:
        self.calls += 1
        raise ConnectionResetError(104, "Connection reset by peer")


class BrokenTransport:
    """Raises a non-OSError (TypeError) on every call — a bug, never a network issue."""

    def __init__(self, method: str):
        self.method = method
        self.calls = 0

    def get(self, url: str, headers: dict) -> tuple[int, str]:
        self.calls += 1
        raise TypeError("unexpected keyword argument")

    def post(self, url: str, headers: dict, body: str, content_type: str) -> tuple[int, str]:
        self.calls += 1
        raise TypeError("unexpected keyword argument")


class SuccessThenResetTransport:
    """Answers from ``pages`` in order, then raises ConnectionResetError on every
    later call — a mid-pagination reset like the live crash."""

    def __init__(self, pages: list[tuple[int, str]]):
        self.pages = pages
        self.calls = 0

    def _next(self) -> tuple[int, str]:
        self.calls += 1
        if self.calls <= len(self.pages):
            return self.pages[self.calls - 1]
        raise ConnectionResetError(104, "Connection reset by peer")

    def get(self, url: str, headers: dict) -> tuple[int, str]:
        return self._next()

    def post(self, url: str, headers: dict, body: str, content_type: str) -> tuple[int, str]:
        return self._next()


class SleepRecorder:
    def __init__(self):
        self.sleeps: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def reddit_page(after: str | None = None) -> str:
    row = {
        "permalink": "/r/energy/comments/abc/test_post/",
        "author": "gridwatch",
        "title": "Transformer backlog",
        "selftext": "",
        "created_utc": 1769904000,
        "score": 10,
        "num_comments": 3,
    }
    return json.dumps({"success": True, "posts": [row], "after": after})


def x_envelope(cursor: str | None = None) -> str:
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
            "output": {"found": True, "data": {"items": [row], "nextCursor": cursor}},
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


def test_non_timeout_urlerror_degrades_to_page_fetch_error_after_retries(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """Behavior change (PR #38): a certificate-style URLError degrades the page
    after the retry budget instead of aborting the fetch on attempt one."""
    sleep = SleepRecorder()
    transport = AlwaysUrlErrorTransport("get")
    with caplog.at_level(logging.WARNING, logger="pipelines.sentiment.fetchers"):
        with pytest.raises(PageFetchError) as excinfo:
            reddit_fetcher(tmp_path, transport, sleep).fetch(
                "topic-a", [("energy", "grid")], WINDOW_END
            )
    message = str(excinfo.value)
    assert CERT_ERROR_TEXT in message
    assert transport.calls == 4  # max_retries + 1 attempts, same budget
    assert sleep.sleeps == [5.0, 10.0, 20.0]
    assert len([r for r in caplog.records if "transport error" in r.getMessage()]) == 4
    assert list(tmp_path.glob("*.json")) == []  # failed attempts are never cached


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


def test_urllib_transport_converts_non_timeout_urlerror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Behavior change (PR #38): the transport classifies non-timeout OSErrors
    (certificate failures, DNS, refused) as TransportError instead of
    re-raising them raw — no socket error may escape the transport boundary."""

    def raise_urlerror(request, timeout):
        raise urllib.error.URLError(CERT_ERROR_TEXT)

    monkeypatch.setattr(urllib.request, "urlopen", raise_urlerror)
    with pytest.raises(TransportError) as excinfo:
        UrllibTransport().get("https://treg.to/call/x", {})
    assert CERT_ERROR_TEXT in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, urllib.error.URLError)


def test_urllib_transport_converts_body_read_connection_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The live-crash seam: a ConnectionResetError raised while reading the
    response body (urllib never wraps it in URLError) becomes TransportError."""

    def reset_mid_body(request, timeout):
        raise ConnectionResetError(104, "Connection reset by peer")

    monkeypatch.setattr(urllib.request, "urlopen", reset_mid_body)
    with pytest.raises(TransportError) as excinfo:
        UrllibTransport().post("https://treg.to/call/x", {}, "{}", "application/json")
    assert "ConnectionResetError" in str(excinfo.value)
    assert "Connection reset by peer" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, ConnectionResetError)


# --- connection-reset regression: the live-crash seam (raw OSError from read) --


def test_get_transient_connection_reset_is_retried_within_existing_budget(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """ConnectionResetError mid-body-read (the live crash) retries and succeeds."""
    sleep = SleepRecorder()
    transport = ConnectionResetThenTransport([(200, reddit_page())])
    with caplog.at_level(logging.WARNING, logger="pipelines.sentiment.fetchers"):
        posts = reddit_fetcher(tmp_path, transport, sleep).fetch(
            "topic-a", [("energy", "grid")], WINDOW_END
        )
    assert len(posts) == 1
    assert transport.calls == 2  # one reset, one success
    assert sleep.sleeps == [5.0]  # one backoff sleep, existing budget
    warnings = [r for r in caplog.records if "transport error" in r.getMessage()]
    assert len(warnings) == 1


def test_get_persistent_connection_reset_mid_pagination_degrades_with_shortfall(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """Persistent resets on page 2 after page 1 banked: PageFetchError degrades to
    a shortfall, the posts already fetched are kept, and the scan continues."""
    sleep = SleepRecorder()
    transport = SuccessThenResetTransport([(200, reddit_page(after="t3_cursor1"))])
    with caplog.at_level(logging.WARNING, logger="pipelines.sentiment.fetchers"):
        fetcher = reddit_fetcher(tmp_path, transport, sleep)
        posts = fetcher.fetch("topic-a", [("energy", "grid")], WINDOW_END)
    assert len(posts) == 1  # page 1's posts survive
    assert transport.calls == 5  # page 1 once + page 2's max_retries + 1 attempts
    assert sleep.sleeps == [5.0, 10.0, 20.0]  # page 2's exponential backoff
    assert fetcher.failed_pages == {REDDIT_TREG_ENDPOINT: 1}
    assert fetcher.failed_queries == {REDDIT_TREG_ENDPOINT: 1}
    assert fetcher.successful_pages == {REDDIT_TREG_ENDPOINT: 1}
    # Only the successful page 1 is cached; failed attempts never are.
    assert len(list(tmp_path.glob("*.json"))) == 1
    assert len([r for r in caplog.records if "failed after retries" in r.getMessage()]) == 1


def test_post_transient_connection_reset_is_retried_within_existing_budget(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """Gateway POST path: a single ConnectionResetError retries and succeeds."""
    sleep = SleepRecorder()
    transport = ConnectionResetThenTransport([(200, x_envelope())])
    with caplog.at_level(logging.WARNING, logger="pipelines.sentiment.fetchers"):
        posts = x_fetcher(tmp_path, transport, sleep).fetch(
            "topic-a", "x-indexed", ["grid"], WINDOW_END
        )
    assert len(posts) == 1
    assert transport.calls == 2
    assert sleep.sleeps == [5.0]
    assert len([r for r in caplog.records if "transport error" in r.getMessage()]) == 1


def test_post_persistent_connection_reset_mid_pagination_degrades_with_shortfall(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """Gateway POST path: persistent resets on page 2 degrade to a shortfall and
    the scan continues with page 1's posts, matching the GET-path semantics."""
    sleep = SleepRecorder()
    transport = SuccessThenResetTransport([(200, x_envelope(cursor="cursor-1"))])
    with caplog.at_level(logging.WARNING, logger="pipelines.sentiment.fetchers"):
        fetcher = x_fetcher(tmp_path, transport, sleep)
        posts = fetcher.fetch("topic-a", "x-indexed", ["grid"], WINDOW_END)
    assert len(posts) == 1
    assert transport.calls == 5
    assert sleep.sleeps == [5.0, 10.0, 20.0]
    endpoint_id = TREG_ENDPOINT["x-indexed"]
    assert fetcher.failed_pages == {endpoint_id: 1}
    assert fetcher.failed_queries == {endpoint_id: 1}
    assert fetcher.successful_pages == {endpoint_id: 1}
    assert len(list(tmp_path.glob("*.json"))) == 1  # only page 1 cached
    assert len([r for r in caplog.records if "failed after retries" in r.getMessage()]) == 1


# --- non-network bugs still propagate loudly ------------------------------------


def test_non_oserror_transport_bug_propagates_on_get(tmp_path: Path):
    """A TypeError from the transport is a bug, not a network issue: it must
    escape immediately instead of being retried or wrapped."""
    sleep = SleepRecorder()
    transport = BrokenTransport("get")
    with pytest.raises(TypeError):
        reddit_fetcher(tmp_path, transport, sleep).fetch(
            "topic-a", [("energy", "grid")], WINDOW_END
        )
    assert transport.calls == 1  # no retries
    assert sleep.sleeps == []  # no backoff
    assert list(tmp_path.glob("*.json")) == []


def test_non_oserror_transport_bug_propagates_on_post(tmp_path: Path):
    """Same loud propagation on the gateway POST path."""
    sleep = SleepRecorder()
    transport = BrokenTransport("post")
    with pytest.raises(TypeError):
        x_fetcher(tmp_path, transport, sleep).fetch("topic-a", "x-indexed", ["grid"], WINDOW_END)
    assert transport.calls == 1
    assert sleep.sleeps == []
    assert list(tmp_path.glob("*.json")) == []
