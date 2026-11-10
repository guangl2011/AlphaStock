"""Tiingo EOD fetcher: request pacing, exponential backoff, on-disk cache.

Operational constraints observed during coverage validation (2026-11-10):

* Rolling quota of roughly 50 requests/hour on the current subscription.
  A daily run of 200 tickers fits, but only with pacing — default 3s between
  requests — and the cache is what makes re-runs cheap: a date already on
  disk is never re-fetched.
* HTTP 429 responses carry no rate-limit headers, so backoff is time-based:
  wait at least 60s, double per retry, bounded by ``max_retries``.
* The per-ticker prices endpoint
  (``/tiingo/daily/{ticker}/prices?startDate=&endDate=``) is used; the bulk
  ``tickers=`` endpoint with date parameters is deprecated server-side.

The API key is accepted only as a constructor argument — it is never logged
and never written to the cache or any output.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Protocol

from .engine import EodRecord

logger = logging.getLogger(__name__)

TIINGO_PRICES_URL = "https://api.tiingo.com/tiingo/daily/{ticker}/prices"

DEFAULT_PACE_SECONDS = 3.0
DEFAULT_BACKOFF_BASE_SECONDS = 60.0
DEFAULT_MAX_RETRIES = 3


class FetchError(RuntimeError):
    """Raised when Tiingo cannot be reached or answers unrecoverably."""


class Transport(Protocol):
    """Minimal HTTP surface so tests never touch the network."""

    def get(self, url: str, headers: dict[str, str]) -> tuple[int, str]: ...


class UrllibTransport:
    """Default stdlib transport."""

    def get(self, url: str, headers: dict[str, str]) -> tuple[int, str]:
        request = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return response.status, response.read().decode("utf-8")


class TiingoEodClient:
    """Fetches and caches per-ticker EOD history from Tiingo.

    The cache layout is deterministic: ``{cache_dir}/{ticker}.json`` holds a
    JSON object mapping ISO dates to normalized rows, sorted by date. Any
    requested date already present in the cache is served from disk without
    a network call, so re-runs and backfills never burn quota twice.
    """

    def __init__(
        self,
        api_key: str,
        cache_dir: Path | str,
        *,
        pace_seconds: float = DEFAULT_PACE_SECONDS,
        backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise FetchError("Tiingo API key is empty — refusing to run live mode")
        self._api_key = api_key
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._pace = pace_seconds
        self._backoff_base = backoff_base_seconds
        self._max_retries = max_retries
        self._transport = transport or UrllibTransport()
        self._sleep = sleep

    # -- public API ---------------------------------------------------------

    def fetch_range(
        self, tickers: list[str], start: date, end: date
    ) -> dict[str, list[EodRecord]]:
        """EOD records per ticker for [start, end], from cache when possible."""
        out: dict[str, list[EodRecord]] = {}
        for i, ticker in enumerate(sorted(tickers)):
            if i:
                self._sleep(self._pace)
            out[ticker] = self.fetch_ticker(ticker, start, end)
        return out

    def fetch_ticker(self, ticker: str, start: date, end: date) -> list[EodRecord]:
        """One ticker's records; network only for windows missing from the cache."""
        windows, rows = self._load_cache(ticker)
        if _windows_cover(windows, start, end):
            return self._records_in_range(rows, start, end)

        fetched = self._fetch_network(ticker, start, end)
        rows.update(fetched)
        windows = _merged_windows(windows + [(start, end)])
        self._save_cache(ticker, windows, rows)
        return self._records_in_range(rows, start, end)

    # -- internals ----------------------------------------------------------

    def _fetch_network(self, ticker: str, start: date, end: date) -> dict[str, dict]:
        url = (
            f"{TIINGO_PRICES_URL.format(ticker=ticker)}"
            f"?startDate={start.isoformat()}&endDate={end.isoformat()}"
            f"&format=json"
        )
        headers = {
            "Token": self._api_key,
            "Content-Type": "application/json",
            "User-Agent": "alphastock-pipelines",
        }
        last_error = ""
        for attempt in range(self._max_retries + 1):
            try:
                status, body = self._transport.get(url, headers)
            except urllib.error.HTTPError as exc:
                status, body = exc.code, exc.read().decode("utf-8", errors="replace")
            except urllib.error.URLError as exc:
                raise FetchError(f"Tiingo request for {ticker} failed: {exc.reason}") from exc

            if status == 200:
                return _parse_prices(body)
            if status == 429:
                # No rate-limit headers observed on Tiingo 429s: wait it out.
                wait = self._backoff_base * (2**attempt)
                last_error = f"HTTP 429 on attempt {attempt + 1}"
                logger.warning(
                    "Tiingo 429 for %s (%s); backing off %.0fs", ticker, last_error, wait
                )
                if attempt < self._max_retries:
                    self._sleep(wait)
                continue
            raise FetchError(f"Tiingo returned HTTP {status} for {ticker}: {body[:200]}")

        raise FetchError(
            f"Tiingo rate limit persisted for {ticker} after {self._max_retries + 1} "
            f"attempts ({last_error}); run again later — the cache keeps earlier progress"
        )

    def _cache_path(self, ticker: str) -> Path:
        return self._cache_dir / f"{ticker}.json"

    def _load_cache(self, ticker: str) -> tuple[list[tuple[date, date]], dict[str, dict]]:
        path = self._cache_path(ticker)
        if not path.is_file():
            return [], {}
        with path.open(encoding="utf-8") as f:
            payload = json.load(f)
        windows = [
            (date.fromisoformat(w[0]), date.fromisoformat(w[1]))
            for w in payload.get("windows", [])
        ]
        rows = {str(k): dict(v) for k, v in payload.get("rows", {}).items()}
        return windows, rows

    def _save_cache(
        self, ticker: str, windows: list[tuple[date, date]], rows: dict[str, dict]
    ) -> None:
        path = self._cache_path(ticker)
        tmp = path.with_suffix(".tmp")
        payload = {
            "windows": [[w[0].isoformat(), w[1].isoformat()] for w in windows],
            "rows": dict(sorted(rows.items())),
        }
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
        tmp.replace(path)

    @staticmethod
    def _records_in_range(
        rows: dict[str, dict], start: date, end: date
    ) -> list[EodRecord]:
        out = []
        for day in sorted(rows):
            d = date.fromisoformat(day)
            if start <= d <= end:
                out.append(EodRecord(d, float(rows[day]["close"]), float(rows[day]["adjClose"])))
        return out


def _parse_prices(body: str) -> dict[str, dict]:
    """Parse a Tiingo prices response into {iso_date: {close, adjClose}}.

    An empty array is a valid answer (an unresolvable ticker) — the caller
    decides whether that is a documented exclusion.
    """
    rows = json.loads(body)
    if not isinstance(rows, list):
        raise FetchError(f"unexpected Tiingo payload shape: {type(rows).__name__}")
    out: dict[str, dict] = {}
    for row in rows:
        day = str(row.get("date", ""))[:10]
        if not day or row.get("close") is None or row.get("adjClose") is None:
            raise FetchError(f"Tiingo row missing date/close/adjClose: {row!r}")
        out[day] = {"close": row["close"], "adjClose": row["adjClose"]}
    return out


def _merged_windows(
    windows: list[tuple[date, date]],
) -> list[tuple[date, date]]:
    """Union of fetched [start, end] windows, merged where they overlap or touch."""
    out: list[list[date]] = []
    for start, end in sorted(windows):
        if start > end:
            raise FetchError(f"invalid cache window {start}..{end}")
        if out and start <= out[-1][1]:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return [(w[0], w[1]) for w in out]


def _windows_cover(windows: list[tuple[date, date]], start: date, end: date) -> bool:
    """Whether already-fetched windows span [start, end].

    The cache is the fetched record: a 200 answer with zero rows still marks
    its window covered, so an unresolvable ticker is not re-requested on
    every run. Missing closes inside a covered window surface loudly at the
    engine, never by re-hitting the API silently.
    """
    return any(w_start <= start and end <= w_end for w_start, w_end in windows)
