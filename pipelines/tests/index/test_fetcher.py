"""Tests for the Tiingo fetcher: pacing, backoff, cache. All offline —
a fake transport stands in for HTTP; the real API is never touched in CI.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

import pytest

from pipelines.indices.fetcher import (
    DEFAULT_BACKOFF_BASE_SECONDS,
    FetchError,
    TiingoEodClient,
    _parse_prices,
)

T1 = date(2026, 11, 6)
T2 = date(2026, 11, 9)


class FakeTransport:
    """Scripted transport: pops (status, body) responses per ticker."""

    def __init__(self, responses: dict[str, list[tuple[int, str]]]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str, headers: dict[str, str]) -> tuple[int, str]:
        ticker = url.split("/daily/")[1].split("/")[0]
        self.calls.append(ticker)
        script = self.responses.get(ticker, [])
        if not script:
            raise AssertionError(f"unexpected request for {ticker}: {url}")
        return script.pop(0)


class SleepSpy:
    def __init__(self) -> None:
        self.waits: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


def _body(rows: list[dict]) -> str:
    return json.dumps(rows)


def _client(tmp_path: Path, transport: FakeTransport) -> tuple[TiingoEodClient, SleepSpy]:
    sleep = SleepSpy()
    client = TiingoEodClient(
        "test-key", tmp_path / "cache", pace_seconds=1.0, transport=transport, sleep=sleep
    )
    return client, sleep


class TestCache:
    def test_cached_dates_are_not_refetched(self, tmp_path: Path) -> None:
        transport = FakeTransport({"AAA": [(200, _body([
            {"date": "2026-11-06T00:00:00.000000000", "close": 10.0, "adjClose": 10.0},
            {"date": "2026-11-09T00:00:00.000000000", "close": 11.0, "adjClose": 11.0},
        ]))]})
        client, _ = _client(tmp_path, transport)
        first = client.fetch_ticker("AAA", T1, T2)
        assert len(first) == 2
        # Second call: cache spans the window, zero network.
        second = client.fetch_ticker("AAA", T1, T2)
        assert len(transport.calls) == 1
        assert [(r.close, r.adj_close) for r in second] == [(10.0, 10.0), (11.0, 11.0)]

    def test_partial_cache_extends_without_refetching_old_dates(
        self, tmp_path: Path
    ) -> None:
        """A window inside an already-fetched span is served from cache."""
        transport = FakeTransport({"AAA": [(200, _body([
            {"date": "2026-11-06T00:00:00.000000000", "close": 10.0, "adjClose": 10.0},
            {"date": "2026-11-09T00:00:00.000000000", "close": 11.0, "adjClose": 11.0},
        ]))]})
        client, _ = _client(tmp_path, transport)
        client.fetch_ticker("AAA", T1, T2)
        rows = client.fetch_ticker("AAA", T1, T1)  # sub-window: cached
        assert len(transport.calls) == 1
        assert len(rows) == 1

    def test_unfetched_gap_between_cached_days_goes_to_network(
        self, tmp_path: Path
    ) -> None:
        """Two disjoint single-day fetches do NOT cover the span between them —
        the intervening trading day was never requested, so it must be."""
        transport = FakeTransport({"AAA": [
            (200, _body([
                {"date": "2026-11-06T00:00:00.000000000", "close": 10.0, "adjClose": 10.0},
            ])),
            (200, _body([
                {"date": "2026-11-09T00:00:00.000000000", "close": 11.0, "adjClose": 11.0},
            ])),
            (200, _body([])),  # the gap day 2026-11-07 (weekend, legitimately empty)
        ]})
        client, _ = _client(tmp_path, transport)
        client.fetch_ticker("AAA", T1, T1)
        client.fetch_ticker("AAA", T2, T2)
        client.fetch_ticker("AAA", T1, T2)
        assert len(transport.calls) == 3

    def test_cache_is_deterministic_json(self, tmp_path: Path) -> None:
        transport = FakeTransport({"AAA": [(200, _body([
            {"date": "2026-11-09T00:00:00.000000000", "close": 11.0, "adjClose": 11.0},
            {"date": "2026-11-06T00:00:00.000000000", "close": 10.0, "adjClose": 10.0},
        ]))]})
        client, _ = _client(tmp_path, transport)
        client.fetch_ticker("AAA", T1, T2)
        saved = json.loads((tmp_path / "cache" / "AAA.json").read_text())
        assert list(saved["rows"]) == ["2026-11-06", "2026-11-09"]
        assert saved["windows"] == [["2026-11-06", "2026-11-09"]]

    def test_empty_response_is_valid_and_cached(self, tmp_path: Path) -> None:
        """A zero-row answer (unresolvable ticker) is a valid, cached result."""
        transport = FakeTransport({"PSTG": [(200, _body([]))]})
        client, _ = _client(tmp_path, transport)
        assert client.fetch_ticker("PSTG", T1, T2) == []
        assert client.fetch_ticker("PSTG", T1, T2) == []
        assert len(transport.calls) == 1


class TestPacing:
    def test_pace_sleep_between_tickers_only(self, tmp_path: Path) -> None:
        transport = FakeTransport({
            t: [(200, _body([{"date": "2026-11-06T00:00:00.000000000",
                              "close": 10.0, "adjClose": 10.0}]))]
            for t in ("AAA", "BBB", "CCC")
        })
        client, sleep = _client(tmp_path, transport)
        client.fetch_range(["AAA", "BBB", "CCC"], T1, T1)
        assert sleep.waits == [1.0, 1.0]  # no sleep before the first request


class TestBackoff:
    def test_429_then_success_backs_off_at_least_60s(self, tmp_path: Path) -> None:
        transport = FakeTransport({"AAA": [
            (429, "rate limited"),
            (200, _body([{"date": "2026-11-06T00:00:00.000000000",
                          "close": 10.0, "adjClose": 10.0}])),
        ]})
        client, sleep = _client(tmp_path, transport)
        rows = client.fetch_ticker("AAA", T1, T1)
        assert len(rows) == 1
        assert sleep.waits == [DEFAULT_BACKOFF_BASE_SECONDS]
        assert sleep.waits[0] >= 60.0

    def test_429_backoff_is_exponential(self, tmp_path: Path) -> None:
        transport = FakeTransport({"AAA": [
            (429, "rate limited"), (429, "rate limited"),
            (200, _body([{"date": "2026-11-06T00:00:00.000000000",
                          "close": 10.0, "adjClose": 10.0}])),
        ]})
        client, sleep = _client(tmp_path, transport)
        client.fetch_ticker("AAA", T1, T1)
        assert sleep.waits == [60.0, 120.0]

    def test_persistent_429_raises_with_cache_preserved(self, tmp_path: Path) -> None:
        transport = FakeTransport({"AAA": [(429, "rate limited")] * 10})
        client, _ = _client(tmp_path, transport)
        with pytest.raises(FetchError, match="rate limit persisted"):
            client.fetch_ticker("AAA", T1, T1)

    def test_429_warning_is_loud(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        transport = FakeTransport({"AAA": [
            (429, "rate limited"),
            (200, _body([{"date": "2026-11-06T00:00:00.000000000",
                          "close": 10.0, "adjClose": 10.0}])),
        ]})
        client, _ = _client(tmp_path, transport)
        with caplog.at_level(logging.WARNING):
            client.fetch_ticker("AAA", T1, T1)
        assert "429" in caplog.text and "backing off" in caplog.text

    def test_http_error_is_loud(self, tmp_path: Path) -> None:
        transport = FakeTransport({"AAA": [(404, "not found")]})
        client, _ = _client(tmp_path, transport)
        with pytest.raises(FetchError, match="HTTP 404"):
            client.fetch_ticker("AAA", T1, T1)

    def test_empty_api_key_refused(self, tmp_path: Path) -> None:
        with pytest.raises(FetchError, match="API key is empty"):
            TiingoEodClient("", tmp_path / "cache")


class TestParse:
    def test_missing_fields_are_loud(self) -> None:
        with pytest.raises(FetchError, match="missing date/close/adjClose"):
            _parse_prices(_body([{"date": "2026-11-06T00:00:00.000000000", "close": 1.0}]))

    def test_non_list_payload_is_loud(self) -> None:
        with pytest.raises(FetchError, match="unexpected Tiingo payload"):
            _parse_prices(_body({"detail": "error"}))
