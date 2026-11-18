"""Fetchers for the Market Sentiment scan: Reddit native, You.com-indexed X/LinkedIn.

Operational constraints, mirroring the Tiingo EOD fetcher's conventions
(pipelines.indices.fetcher):

* Reddit's public JSON search endpoints work at modest scale with a
  descriptive User-Agent and policy compliance. Requests are paced; HTTP 429
  answers with exponential time-based backoff.
* There is no free general-purpose API for X or LinkedIn. Both are retrieved
  through You.com Search (``YDC_API_KEY``) scoped per platform via the
  matrix's ``site:`` templates — coverage is only what those indexes expose,
  and every output row says so via its platform label.
* Both fetchers are resumable at exact query granularity: Reddit pages carry
  the page number and window tier in the cache key, You.com keys include the
  freshness window, so a widened window or an interrupted run within the same
  window resumes cheaply without re-fetching cached responses.
* API keys are constructor arguments only — never logged, cached, or written
  to any output.

Tests inject a ``Transport`` and a sleep function, so no test touches the
network or waits on real pacing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

REDDIT_SEARCH_URL = "https://www.reddit.com/r/{subreddit}/search.json"
REDDIT_USER_AGENT = (
    "alphastock-sentiment/1.0 (internal research scan; github.com/guangl2011/AlphaStock)"
)
YDC_SEARCH_URL = "https://ydc-index.io/v1/search"

DEFAULT_PACE_SECONDS = 1.5
DEFAULT_BACKOFF_BASE_SECONDS = 5.0
DEFAULT_MAX_RETRIES = 3
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

# Sampling v2: Reddit's API max is 100 per request; the controller paginates
# through the ``after`` cursor up to 3 pages per (subreddit, query).
REDDIT_PAGE_LIMIT = 100
REDDIT_MAX_PAGES = 3
# You.com ``count`` per query — raise to the account plan's maximum.
YDC_COUNT = 20

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
SNIPPET_MAX_CHARS = 280


class FetchError(RuntimeError):
    """Raised when a platform cannot be reached or answers unrecoverably."""


class Transport(Protocol):
    """Minimal HTTP surface so tests never touch the network."""

    def get(self, url: str, headers: dict[str, str]) -> tuple[int, str]: ...


class UrllibTransport:
    """Default stdlib transport."""

    def get(self, url: str, headers: dict[str, str]) -> tuple[int, str]:
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:  # status code is information, not an escape
            return exc.code, exc.read().decode("utf-8", errors="replace")


@dataclass(frozen=True)
class FetchedPost:
    """One retrieved item, platform-tagged, before scoring."""

    platform: str  # reddit | x-indexed | linkedin-indexed
    author: str
    url: str
    post_date: str | None  # ISO date; null when the platform/index exposes none
    snippet: str
    engagement: float = 0.0  # reddit: score + comments; indexed posts use 0 (proxy flag)


def _truncate(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= SNIPPET_MAX_CHARS else text[: SNIPPET_MAX_CHARS - 1] + "…"


def _cache_path(cache_dir: Path, parts: list[str]) -> Path:
    key = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    return cache_dir / f"{key}.json"


class BaseFetcher:
    """Shared pacing/backoff/cache mechanics for both platform fetchers."""

    def __init__(
        self,
        cache_dir: Path | str,
        *,
        platform: str,
        pace_seconds: float = DEFAULT_PACE_SECONDS,
        backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._platform = platform
        self._pace = pace_seconds
        self._backoff_base = backoff_base_seconds
        self._max_retries = max_retries
        self._transport = transport or UrllibTransport()
        self._sleep = sleep

    def _get_cached(
        self,
        url: str,
        headers: dict[str, str],
        cache_key_parts: list[str],
    ) -> str:
        """Cache-first GET; re-raises persistent retryable failures as FetchError."""
        path = _cache_path(self._cache_dir, cache_key_parts)
        if path.is_file():
            logger.debug("cache hit %s", path)
            return path.read_text(encoding="utf-8")
        last = ""
        for attempt in range(self._max_retries + 1):
            if last:  # a retry: exponential backoff after the previous failure
                self._sleep(self._backoff_base * (2 ** (attempt - 1)))
            status, body = self._transport.get(url, headers)
            if status == 200:
                path.write_text(body, encoding="utf-8")
                return body
            last = f"HTTP {status}"
            if status not in RETRYABLE_STATUSES:
                break
            logger.warning("%s returned %s (attempt %d)", url, status, attempt + 1)
        raise FetchError(f"{self._platform} fetch failed ({last}): {url[:120]}")

    def _pacing_pause(self, first: bool) -> None:
        if not first:
            self._sleep(self._pace)


class RedditFetcher(BaseFetcher):
    """Native Reddit public-JSON search per (subreddit, query)."""

    def __init__(
        self,
        cache_dir: Path | str,
        *,
        pace_seconds: float = DEFAULT_PACE_SECONDS,
        backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(
            cache_dir,
            platform="reddit",
            pace_seconds=pace_seconds,
            backoff_base_seconds=backoff_base_seconds,
            max_retries=max_retries,
            transport=transport,
            sleep=sleep,
        )

    def fetch(
        self,
        topic_id: str,
        queries: list[tuple[str, str]],
        window_end: date,
        *,
        t: str = "week",
    ) -> list[FetchedPost]:
        """Posts for each (subreddit, query), paginating up to 3 pages of 100.

        ``t`` is Reddit's window bucket (``week`` | ``month``); the sampling
        controller widens it on L2. Each page is cached under its own key, so
        resume is page-exact.
        """
        posts: list[FetchedPost] = []
        seen: set[str] = set()
        first = True
        for subreddit, query in queries:
            after: str | None = None
            for page in range(1, REDDIT_MAX_PAGES + 1):
                params = {
                    "q": query,
                    "restrict_sr": "1",
                    "sort": "new",
                    "t": t,
                    "limit": str(REDDIT_PAGE_LIMIT),
                }
                if after:
                    params["after"] = after
                url = (
                    REDDIT_SEARCH_URL.format(subreddit=urllib.parse.quote(subreddit))
                    + "?"
                    + urllib.parse.urlencode(params)
                )
                body = self._get_cached(
                    url,
                    {"User-Agent": REDDIT_USER_AGENT, "Accept": "application/json"},
                    ["reddit", topic_id, subreddit, query, t, str(page), window_end.isoformat()],
                )
                page_posts, cursor = self._parse_reddit(body)
                for post in page_posts:
                    if post.url not in seen:
                        seen.add(post.url)
                        posts.append(post)
                self._pacing_pause(first)
                first = False
                if not cursor:
                    break
                after = cursor
        return posts

    def _parse_reddit(self, body: str) -> tuple[list[FetchedPost], str | None]:
        """Parse one search page into posts plus the ``after`` cursor, if any."""
        try:
            payload = json.loads(body)
            data = payload["data"]
            children = data["children"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise FetchError(f"reddit response is malformed: {error}") from error
        cursor = data.get("after")
        posts: list[FetchedPost] = []
        for child in children:
            data = child.get("data", {})
            permalink = data.get("permalink")
            if not permalink:
                continue
            created_utc = data.get("created_utc")
            post_date = (
                datetime.fromtimestamp(created_utc, tz=UTC).date().isoformat()
                if isinstance(created_utc, (int, float))
                else None
            )
            title = data.get("title") or ""
            selftext = data.get("selftext") or ""
            snippet = _truncate(f"{title} — {selftext}" if selftext else title)
            engagement = float(data.get("score") or 0) + float(data.get("num_comments") or 0)
            posts.append(
                FetchedPost(
                    platform="reddit",
                    author=str(data.get("author") or "[unknown]"),
                    url=f"https://www.reddit.com{permalink}",
                    post_date=post_date,
                    snippet=snippet,
                    engagement=engagement,
                )
            )
        return posts, cursor


class YouComFetcher(BaseFetcher):
    """X and LinkedIn mentions via You.com Search, scoped by the matrix templates."""

    def __init__(
        self,
        api_key: str,
        cache_dir: Path | str,
        *,
        count: int = YDC_COUNT,
        pace_seconds: float = DEFAULT_PACE_SECONDS,
        backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise FetchError("You.com API key is empty — refusing to run indexed mode")
        self._api_key = api_key
        self._count = count
        super().__init__(
            cache_dir,
            platform="you.com",
            pace_seconds=pace_seconds,
            backoff_base_seconds=backoff_base_seconds,
            max_retries=max_retries,
            transport=transport,
            sleep=sleep,
        )

    def fetch(
        self,
        topic_id: str,
        platform: str,
        queries: list[str],
        window_end: date,
        *,
        window_days: int = 7,
    ) -> list[FetchedPost]:
        """Posts for ``platform`` (``x-indexed`` | ``linkedin-indexed``) over the topic window.

        One You.com query per keyword x variant (the caller passes the expanded
        list); the freshness window widens on the controller's L2. Cache keys
        include the freshness window, so resume stays exact across widened
        windows.
        """
        if platform not in ("x-indexed", "linkedin-indexed"):
            raise FetchError(f"unknown indexed platform: {platform}")
        window_start = window_end - timedelta(days=window_days)
        posts: list[FetchedPost] = []
        seen: set[str] = set()
        first = True
        for query in queries:
            url = f"{YDC_SEARCH_URL}?" + urllib.parse.urlencode(
                {
                    "query": query,
                    "count": str(self._count),
                    "freshness": f"{window_start}to{window_end}",
                }
            )
            body = self._get_cached(
                url,
                {
                    "X-API-Key": self._api_key,
                    "User-Agent": "alphastock-pipelines",
                    "Accept": "application/json",
                },
                [
                    "youcom",
                    topic_id,
                    platform,
                    query,
                    window_start.isoformat(),
                    window_end.isoformat(),
                ],
            )
            for post in self._parse_results(body, platform):
                if post.url not in seen:
                    seen.add(post.url)
                    posts.append(post)
            self._pacing_pause(first)
            first = False
        return posts

    def _parse_results(self, body: str, platform: str) -> list[FetchedPost]:
        try:
            payload = json.loads(body)
            items = (payload.get("results") or {}).get("web") or []
        except json.JSONDecodeError as error:
            raise FetchError(f"you.com response is malformed: {error}") from error
        posts: list[FetchedPost] = []
        for item in items:
            url = item.get("url")
            if not url:
                continue
            page_age = item.get("page_age") or ""
            date_match = DATE_RE.search(page_age)
            texts = [item.get("title") or "", item.get("description") or ""]
            texts += list(item.get("snippets") or [])
            snippet = _truncate(" — ".join(t for t in texts if t))
            posts.append(
                FetchedPost(
                    platform=platform,
                    author=str(item.get("source") or "[unknown]"),
                    url=url,
                    post_date=date_match.group(0) if date_match else None,
                    snippet=snippet,
                )
            )
        return posts
