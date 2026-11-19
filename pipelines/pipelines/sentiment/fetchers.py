"""Fetchers for the Market Sentiment scan: all platforms via the treg.to gateway.

* Reddit is site-wide keyword search via ``scrapecreators.reddit.search.posts``
  — a **GET** to ``https://treg.to/call/{endpoint}`` answering the provider's
  raw shape with treg billing counters injected:
  ``{success: bool, posts: [...], after: str|null, credits_charged, credits_remaining}``
  (no ``output.found``/``output.data`` wrapper). The endpoint ignores the
  subreddit parameter (verified live 2026-11-18: results span unrelated
  subreddits), so the fetch layer dedupes the matrix's (subreddit, query)
  pairs to unique queries and searches site-wide. ``sort=new`` and the ``t``
  window filter are honored; ``limit`` is not (page size is fixed at 25).
  Pagination follows the ``after`` cursor.
* X and LinkedIn have no free general-purpose API; both are POSTed to
  ``https://treg.to/call/{endpoint_id}`` with the ``X-Treg-Token`` header —
  never ``Authorization`` — and answer an envelope of
  ``{provider, resultId, replayed, costUsd, output: {found, data}}``.
  X posts come from ``anyapi.x.search.posts`` (advanced-search syntax, window
  via ``since:/until:`` in the query), LinkedIn posts from
  ``anyapi.linkedin.search.posts`` (window via the ``datePosted`` enum).
  Coverage is only what those indexes expose, and every output row says so
  via its platform label. An optional ``TREG_MAX_COST`` is forwarded as
  ``X-Treg-Route-Max-Cost`` for spend capping.
* All fetchers are resumable at exact query granularity: pages carry the page
  number and window in the cache key, so a widened window or an interrupted
  run within the same window resumes cheaply without re-fetching cached
  responses.
* Credentials are constructor arguments or environment variables only —
  never logged, cached, or written to any output (the token travels only in
  the ``X-Treg-Token`` header).

Tests inject a ``Transport`` and a sleep function, so no test touches the
network or waits on real pacing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
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

# Reddit runs through the same gateway but a different dialect and method:
# a GET to scrapecreators.reddit.search.posts answering the provider's raw
# shape with treg billing fields injected — {success: bool, posts: [...],
# after: str|null, credits_charged, credits_remaining} — no
# output.found/output.data wrapper. Site-wide keyword search: the subreddit
# parameter is ignored server-side (verified live 2026-11-18), sort=new and
# the t window filter are honored, and limit is NOT honored (page size is
# fixed at 25, so it is never sent). Pagination follows the after cursor.
REDDIT_TREG_ENDPOINT = "scrapecreators.reddit.search.posts"
REDDIT_TREG_PAGE_SIZE = 25  # fixed server-side; documented, never requested
REDDIT_URL_PREFIX = "https://www.reddit.com"
# treg.to gateway: POST https://treg.to/call/{endpoint_id}, authenticated by
# X-Treg-Token (never Authorization). Endpoints verified live 2026-11-18:
#   anyapi.x.search.posts        -> output.data.items (X advanced-search rows)
#   anyapi.linkedin.search.posts -> output.data.posts (LinkedIn post rows)
TREG_CALL_URL = "https://treg.to/call/{endpoint_id}"
TREG_TOKEN_ENV = "TREG_TOKEN"
TREG_MAX_COST_ENV = "TREG_MAX_COST"
TREG_ENDPOINT = {
    "x-indexed": "anyapi.x.search.posts",
    "linkedin-indexed": "anyapi.linkedin.search.posts",
}
TREG_ROWS_KEY = {"x-indexed": "items", "linkedin-indexed": "posts"}
# X advanced search ANDs bare terms, so the matrix's keyword variants are
# joined with explicit OR; the window is appended as since:/until: dates.
TREG_X_PAGE_LIMIT = 50  # the endpoint's maximum page size (1-50)
TREG_MAX_PAGES = 3
# Required row keys per platform: a row missing any of these is a malformed
# response, not a partially-mapped post — loud failure, never silent skipping.
TREG_ROW_KEYS = {
    "x-indexed": (
        "url",
        "text",
        "authorUsername",
        "createdUtc",
        "likeCount",
        "retweetCount",
        "viewCount",
    ),
    "linkedin-indexed": (
        "url",
        "text",
        "authorName",
        "createdUtc",
        "reactionCount",
        "commentCount",
    ),
}
# The subset that must be real numbers (createdUtc is unix seconds; the rest
# feed the engagement proxy).
TREG_NUMERIC_KEYS = {
    "x-indexed": ("createdUtc", "likeCount", "retweetCount", "viewCount"),
    "linkedin-indexed": ("createdUtc", "reactionCount", "commentCount"),
}
# Reddit rows: same strict contract as the envelope dialects — a row missing
# any pinned key, or carrying a non-numeric count/timestamp, is a malformed
# response, never a partially-mapped post. The scan layer dedupes by URL +
# normalized-text hash on top of the fetcher's URL dedupe.
REDDIT_TREG_ROW_KEYS = (
    "permalink",
    "author",
    "title",
    "selftext",
    "created_utc",
    "score",
    "num_comments",
)
REDDIT_TREG_NUMERIC_KEYS = ("created_utc", "score", "num_comments")

DEFAULT_PACE_SECONDS = 1.5
DEFAULT_BACKOFF_BASE_SECONDS = 5.0
DEFAULT_MAX_RETRIES = 3
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

# Sampling v2: the controller paginates through the ``after`` cursor up to
# 3 pages per unique query (25 rows/page server-side).
REDDIT_TREG_MAX_PAGES = 3

SNIPPET_MAX_CHARS = 280


class FetchError(RuntimeError):
    """Raised when a platform cannot be reached or answers unrecoverably."""


class PageFetchError(FetchError):
    """One page exhausted its retry budget (persistent 5xx/timeout/network failure).

    A FetchError subclass so every existing handler still matches, but
    distinct enough that the sampling layer can degrade a single bad page to
    a coverage shortfall while credential and shape failures stay loud.
    """


class TransportError(FetchError):
    """Internal: a transport hit a non-timeout network failure — retryable by the loops.

    Wraps raw socket/OSError failures (ConnectionResetError, DNS failure,
    connection refused, TLS errors) that urllib does **not** wrap in URLError
    when they are raised while reading the response body. Subclasses
    ``FetchError`` so anything that ever leaks past the retry loops still
    lands in the established error family, while the loops treat it exactly
    like TransportTimeout: retryable within the existing budget, degrading
    to PageFetchError on exhaustion.
    """


class TransportTimeout(TimeoutError):
    """Internal: a transport hit a connect/read timeout — retryable by the fetch loops.

    Subclasses ``TimeoutError`` so ``_is_timeout`` (and anything else keyed on
    socket-style timeouts) recognizes a converted transport timeout as
    retryable, and the fetch loops absorb it within their existing budget.
    The loops treat it exactly like a retryable HTTP status: it consumes the
    existing max-retries/backoff budget (no separate counter) and becomes a
    FetchError only once that budget is exhausted.
    """


def _is_timeout(error: BaseException) -> bool:
    """True for socket read/connect timeouts, including URLError-wrapped ones.

    ``socket.timeout`` is an alias of ``TimeoutError`` on Python 3.10+; a
    connect timeout surfaces as ``URLError`` with a timeout ``reason``.
    """
    if isinstance(error, TimeoutError):
        return True
    return isinstance(error, urllib.error.URLError) and isinstance(error.reason, TimeoutError)


def _transport_failure_label(error: BaseException) -> str:
    """Log/message label for a retried network failure: timeouts keep their wording."""
    return f"timed out ({error})" if _is_timeout(error) else f"transport error ({error})"


class Transport(Protocol):
    """Minimal HTTP surface so tests never touch the network."""

    def get(self, url: str, headers: dict[str, str]) -> tuple[int, str]: ...

    def post(
        self, url: str, headers: dict[str, str], body: str, content_type: str
    ) -> tuple[int, str]: ...


class UrllibTransport:
    """Default stdlib transport."""

    def get(self, url: str, headers: dict[str, str]) -> tuple[int, str]:
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:  # status code is information, not an escape
            return exc.code, exc.read().decode("utf-8", errors="replace")
        except OSError as exc:  # URLError, timeouts, ConnectionResetError, TLS errors
            if _is_timeout(exc):
                raise TransportTimeout(str(exc)) from exc
            # urllib wraps connect-phase failures in URLError but does NOT wrap
            # errors raised while reading the response body —
            # ConnectionResetError and other OSError subclasses surface raw.
            # Classify every one here: no raw socket error may ever escape
            # the transport boundary.
            raise TransportError(f"{type(exc).__name__}: {exc}") from exc

    def post(
        self, url: str, headers: dict[str, str], body: str, content_type: str
    ) -> tuple[int, str]:
        request = urllib.request.Request(
            url,
            data=body.encode("utf-8"),
            headers={**headers, "Content-Type": content_type},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:  # status code is information, not an escape
            return exc.code, exc.read().decode("utf-8", errors="replace")
        except OSError as exc:  # URLError, timeouts, ConnectionResetError, TLS errors
            if _is_timeout(exc):
                raise TransportTimeout(str(exc)) from exc
            # Same body-read hole as get(): classify, never re-raise raw.
            raise TransportError(f"{type(exc).__name__}: {exc}") from exc


@dataclass(frozen=True)
class FetchedPost:
    """One retrieved item, platform-tagged, before scoring."""

    platform: str  # reddit | x-indexed | linkedin-indexed
    author: str
    url: str
    post_date: str | None  # ISO date; null when the platform/index exposes none
    snippet: str
    engagement: float = 0.0  # reddit: score + comments; indexed posts: platform-count proxy


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
        # Shortfall accounting, by endpoint: pages that answered (live or from
        # cache), pages that exhausted their retry budget, and queries aborted
        # by such a page. Reported in the run's coverage as honest shortfall.
        self.successful_pages: dict[str, int] = {}
        self.failed_pages: dict[str, int] = {}
        self.failed_queries: dict[str, int] = {}

    def _note_page_success(self, endpoint: str) -> None:
        self.successful_pages[endpoint] = self.successful_pages.get(endpoint, 0) + 1

    def _note_page_failure(self, endpoint: str) -> None:
        self.failed_pages[endpoint] = self.failed_pages.get(endpoint, 0) + 1

    def _note_query_failure(self, endpoint: str) -> None:
        self.failed_queries[endpoint] = self.failed_queries.get(endpoint, 0) + 1

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
        retryable = False
        for attempt in range(self._max_retries + 1):
            if last:  # a retry: exponential backoff after the previous failure
                self._sleep(self._backoff_base * (2 ** (attempt - 1)))
            try:
                status, body = self._transport.get(url, headers)
            except (TransportError, TransportTimeout, OSError) as error:
                # Network-level failures are retryable within the same budget —
                # TransportError/TransportTimeout from the real transport, raw
                # OSErrors (URLError, ConnectionResetError) from test doubles
                # or a body-read the transport could not classify. Non-network
                # bugs (TypeError etc.) are not OSError and still propagate.
                last = _transport_failure_label(error)
                retryable = True
                logger.warning("%s %s (attempt %d)", url, last, attempt + 1)
                continue  # retried within the same max-retries/backoff budget
            if status == 200:
                path.write_text(body, encoding="utf-8")
                return body
            last = f"HTTP {status}"
            retryable = status in RETRYABLE_STATUSES
            if not retryable:
                break
            logger.warning("%s returned %s (attempt %d)", url, status, attempt + 1)
        if retryable:
            # The retry budget bought nothing — a persistently bad page, not a
            # dead credential: the scan layer degrades this to a shortfall.
            raise PageFetchError(f"{self._platform} fetch failed ({last}): {url[:120]}")
        raise FetchError(f"{self._platform} fetch failed ({last}): {url[:120]}")

    def _pacing_pause(self, first: bool) -> None:
        if not first:
            self._sleep(self._pace)


class RedditTregFetcher(BaseFetcher):
    """Reddit mentions via the treg.to gateway: site-wide keyword search.

    The endpoint answers a GET to ``scrapecreators.reddit.search.posts`` with
    the provider's raw shape plus treg billing counters —
    ``{success, posts, after, credits_charged, credits_remaining}`` — and it
    ignores the subreddit parameter, so the matrix's (subreddit, query) pairs
    are deduped to unique queries before any call is made: paying per call
    for identical site-wide results would be waste. Each call sends
    ``sort=new`` and the window tier as ``t``; ``limit`` is never sent
    (fixed 25-row page size, verified live). Pagination follows the ``after``
    cursor up to ``REDDIT_TREG_MAX_PAGES`` pages per unique query; each page
    is cached under its own key, so resume stays page-exact.

    Coverage accounting for the site-wide path: ``queries_fetched`` counts
    unique queries requested and ``pages_fetched`` counts pages consumed
    (cached or live) — reported in the run's reddit coverage block instead of
    (subreddit, query) pairs.
    """

    def __init__(
        self,
        cache_dir: Path | str,
        *,
        token: str | None = None,
        max_cost: str | None = None,
        pace_seconds: float = DEFAULT_PACE_SECONDS,
        backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        resolved = token or os.environ.get(TREG_TOKEN_ENV) or ""
        if not resolved:
            raise FetchError(
                "treg.to credentials are missing — every sentiment platform (Reddit "
                "site-wide search, indexed X/LinkedIn) runs through the treg.to gateway, "
                f"which authenticates with the {TREG_TOKEN_ENV} env var. Get a token at "
                "https://treg.to, then add it as a repository secret in GitHub "
                "(Settings → Secrets and variables → Actions → New repository secret, named "
                f"exactly {TREG_TOKEN_ENV}) for the sentiment-smoke workflow, or export it in "
                "your shell for local runs."
            )
        self._token = resolved
        # Optional spend cap, forwarded verbatim as X-Treg-Route-Max-Cost.
        self._max_cost = (
            max_cost if max_cost is not None else os.environ.get(TREG_MAX_COST_ENV) or ""
        )
        super().__init__(
            cache_dir,
            platform="reddit",
            pace_seconds=pace_seconds,
            backoff_base_seconds=backoff_base_seconds,
            max_retries=max_retries,
            transport=transport,
            sleep=sleep,
        )
        self.queries_fetched = 0
        self.pages_fetched = 0

    def _request_headers(self) -> dict[str, str]:
        # The token travels only in X-Treg-Token — never an Authorization header.
        headers = {
            "X-Treg-Token": self._token,
            "User-Agent": "alphastock-pipelines",
            "Accept": "application/json",
        }
        if self._max_cost:
            headers["X-Treg-Route-Max-Cost"] = self._max_cost
        return headers

    def fetch(
        self,
        topic_id: str,
        queries: list[tuple[str, str]],
        window_end: date,
        *,
        t: str = "week",
    ) -> list[FetchedPost]:
        """Posts for each unique query in the (subreddit, query) pairs.

        The subreddit dimension is spent — the endpoint ignores it — so pairs
        collapse to unique queries in first-seen order. ``t`` is Reddit's
        window bucket (``week`` | ``month``); the sampling controller widens
        it on L2. Each page is cached under its own key, so resume is
        page-exact.
        """
        unique_queries = list(dict.fromkeys(query for _subreddit, query in queries))
        self.queries_fetched += len(unique_queries)
        posts: list[FetchedPost] = []
        seen: set[str] = set()
        first = True
        call_successful_pages = 0
        for query in unique_queries:
            after: str | None = None
            for page in range(1, REDDIT_TREG_MAX_PAGES + 1):
                params: dict[str, str] = {"query": query, "sort": "new", "t": t}
                if after:
                    params["after"] = after
                url = (
                    TREG_CALL_URL.format(endpoint_id=REDDIT_TREG_ENDPOINT)
                    + "?"
                    + urllib.parse.urlencode(params)
                )
                try:
                    body = self._get_cached(
                        url,
                        self._request_headers(),
                        ["treg", "reddit", topic_id, query, t, str(page), window_end.isoformat()],
                    )
                    rows, after = self._parse_raw(body)
                except PageFetchError as error:
                    if not call_successful_pages:
                        # Nothing banked yet — a leading failure is a dead
                        # platform (bad token, total outage): fail loudly as
                        # today, never return an all-empty batch.
                        raise
                    # One persistently bad page after progress: stop
                    # paginating this query, keep everything its earlier
                    # pages already contributed, record the shortfall, and
                    # move on to the next query.
                    self._note_page_failure(REDDIT_TREG_ENDPOINT)
                    self._note_query_failure(REDDIT_TREG_ENDPOINT)
                    logger.warning(
                        "reddit query %r page %d failed after retries: %s — keeping "
                        "%d posts fetched so far and continuing the scan",
                        query,
                        page,
                        error,
                        len(posts),
                    )
                    break
                self._note_page_success(REDDIT_TREG_ENDPOINT)
                call_successful_pages += 1
                self.pages_fetched += 1
                for row in rows:
                    post = _map_reddit_row(row)
                    if post.url not in seen:
                        seen.add(post.url)
                        posts.append(post)
                self._pacing_pause(first)
                first = False
                if not after:
                    break
        return posts

    def _parse_raw(self, body: str) -> tuple[list[dict], str | None]:
        """Validate one raw-dialect page; malformed shapes fail loudly with the path.

        Unlike the X/LinkedIn envelope there is no ``output`` wrapper:
        ``success`` is a top-level boolean, rows live under ``posts``, and the
        cursor is the top-level ``after``.
        """
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as error:
            raise FetchError(
                f"treg {REDDIT_TREG_ENDPOINT} response is malformed (not JSON): {error}"
            ) from error
        if not isinstance(payload, dict):
            raise FetchError(
                f"treg {REDDIT_TREG_ENDPOINT} response malformed at root: expected an object"
            )
        success = payload.get("success")
        if not isinstance(success, bool):
            raise FetchError(
                f"treg {REDDIT_TREG_ENDPOINT} response malformed at success: expected a boolean"
            )
        if not success:
            raise FetchError(f"treg {REDDIT_TREG_ENDPOINT} call was unsuccessful (success=false)")
        rows = payload.get("posts")
        if not isinstance(rows, list):
            raise FetchError(
                f"treg {REDDIT_TREG_ENDPOINT} response malformed at posts: expected a list"
            )
        for index, row in enumerate(rows):
            where = f"posts[{index}]"
            if not isinstance(row, dict):
                raise FetchError(
                    f"treg {REDDIT_TREG_ENDPOINT} row {index} malformed at {where}: "
                    "expected an object"
                )
            missing = [key for key in REDDIT_TREG_ROW_KEYS if key not in row]
            if missing:
                raise FetchError(
                    f"treg {REDDIT_TREG_ENDPOINT} row {index} malformed at {where}: "
                    f"missing keys {missing}"
                )
            for key in REDDIT_TREG_NUMERIC_KEYS:
                if isinstance(row[key], bool) or not isinstance(row[key], (int, float)):
                    raise FetchError(
                        f"treg {REDDIT_TREG_ENDPOINT} row {index} malformed at {where}.{key}: "
                        "expected a number"
                    )
        after = payload.get("after")
        if after is not None and not isinstance(after, str):
            raise FetchError(
                f"treg {REDDIT_TREG_ENDPOINT} response malformed at after: "
                "expected a string or null"
            )
        return rows, after


def _reddit_url(permalink: str) -> str:
    """Absolute post URL: full links pass through, scheme- and path-relative get the host."""
    if permalink.startswith("https://") or permalink.startswith("http://"):
        return permalink
    if permalink.startswith("//"):
        return f"https:{permalink}"
    if permalink.startswith("/"):
        return f"{REDDIT_URL_PREFIX}{permalink}"
    return permalink


def _map_reddit_row(row: dict) -> FetchedPost:
    """One scrapecreators.reddit.search.posts row -> the post shape; engagement = score+comments."""
    title = str(row["title"] or "")
    selftext = str(row["selftext"] or "")
    snippet = _truncate(f"{title} — {selftext}" if selftext else title)
    engagement = float(row["score"]) + float(row["num_comments"])
    return FetchedPost(
        platform="reddit",
        author=str(row["author"] or "[unknown]"),
        url=_reddit_url(str(row["permalink"])),
        post_date=_iso_date(row["created_utc"]),
        snippet=snippet,
        engagement=engagement,
    )


class TregFetcher(BaseFetcher):
    """X and LinkedIn mentions via the treg.to gateway, scoped per platform.

    ``x-indexed`` posts come from ``anyapi.x.search.posts``: the matrix's
    keyword variants are OR-joined (X advanced search ANDs bare terms) and the
    sampling window is appended as ``since:/until:``. ``linkedin-indexed``
    posts come from ``anyapi.linkedin.search.posts`` with the window mapped
    onto the ``datePosted`` enum. Both paginate through ``nextCursor`` up to
    ``TREG_MAX_PAGES`` pages per (topic, window); each page is cached under
    its own key, so resume stays exact across widened windows.
    """

    def __init__(
        self,
        cache_dir: Path | str,
        *,
        token: str | None = None,
        max_cost: str | None = None,
        pace_seconds: float = DEFAULT_PACE_SECONDS,
        backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        resolved = token or os.environ.get(TREG_TOKEN_ENV) or ""
        if not resolved:
            raise FetchError(
                "treg.to credentials are missing — indexed X/LinkedIn search runs through the "
                f"treg.to gateway, which authenticates with the {TREG_TOKEN_ENV} env var. Get a "
                "token at https://treg.to, then add it as a repository secret in GitHub "
                "(Settings → Secrets and variables → Actions → New repository secret, named "
                f"exactly {TREG_TOKEN_ENV}) for the sentiment-smoke workflow, or export it in "
                "your shell for local runs."
            )
        self._token = resolved
        # Optional spend cap, forwarded verbatim as X-Treg-Route-Max-Cost.
        self._max_cost = (
            max_cost if max_cost is not None else os.environ.get(TREG_MAX_COST_ENV) or ""
        )
        super().__init__(
            cache_dir,
            platform="treg",
            pace_seconds=pace_seconds,
            backoff_base_seconds=backoff_base_seconds,
            max_retries=max_retries,
            transport=transport,
            sleep=sleep,
        )

    def _request_headers(self) -> dict[str, str]:
        # The token travels only in X-Treg-Token — never an Authorization header.
        headers = {
            "X-Treg-Token": self._token,
            "User-Agent": "alphastock-pipelines",
            "Accept": "application/json",
        }
        if self._max_cost:
            headers["X-Treg-Route-Max-Cost"] = self._max_cost
        return headers

    def _post_cached(
        self, endpoint_id: str, platform: str, body: dict, cache_key_parts: list[str]
    ) -> dict:
        """Cache-first gateway POST; re-raises persistent retryable failures as FetchError.

        A 200 response is validated (JSON parse, then the pinned envelope
        contract) BEFORE it is written to the cache, and validation failures
        retry within this loop's budget — a transient gateway glitch that
        answers 200 with a malformed body must never poison the cache or kill
        the scan; it exhausts the budget as a PageFetchError and degrades to a
        coverage shortfall like any other persistently bad page.
        """
        path = _cache_path(self._cache_dir, cache_key_parts)
        if path.is_file():
            logger.debug("cache hit %s", path)
            return json.loads(path.read_text(encoding="utf-8"))
        url = TREG_CALL_URL.format(endpoint_id=endpoint_id)
        payload = json.dumps(body)
        last = ""
        retryable = False
        for attempt in range(self._max_retries + 1):
            if last:  # a retry: exponential backoff after the previous failure
                self._sleep(self._backoff_base * (2 ** (attempt - 1)))
            try:
                status, text = self._transport.post(
                    url, self._request_headers(), payload, "application/json"
                )
            except (TransportError, TransportTimeout, OSError) as error:
                # Same retryable contract as the GET loop: network-level
                # failures (TransportError/TransportTimeout from the real
                # transport, raw OSErrors from test doubles) consume the
                # existing budget; non-OSError bugs still propagate loudly.
                last = _transport_failure_label(error)
                retryable = True
                logger.warning("%s %s (attempt %d)", url, last, attempt + 1)
                continue  # retried within the same max-retries/backoff budget
            if status == 200:
                try:
                    envelope = json.loads(text)
                except json.JSONDecodeError as error:
                    # A gateway glitch behind a 200 status is retryable like a
                    # 5xx — and is never written to the cache.
                    last = f"response is malformed (not JSON): {error}"
                    retryable = True
                    logger.warning("%s %s (attempt %d)", url, last, attempt + 1)
                    continue
                try:
                    # Validate the pinned envelope contract before caching:
                    # idempotent with the re-parse in fetch(), and the only
                    # gate between a malformed body and path.write_text.
                    self._parse_envelope(envelope, endpoint_id, platform)
                except FetchError as error:
                    last = str(error)
                    retryable = True
                    logger.warning("%s envelope invalid: %s (attempt %d)", url, error, attempt + 1)
                    continue
                path.write_text(text, encoding="utf-8")
                return envelope
            last = f"HTTP {status}"
            retryable = status in RETRYABLE_STATUSES
            if not retryable:
                # A 4xx is a dead end for this call — log it before breaking
                # so a non-retryable storm is visible in the run output.
                logger.warning(
                    "%s returned non-retryable %s (attempt %d)", url, status, attempt + 1
                )
                break
            logger.warning("%s returned %s (attempt %d)", url, status, attempt + 1)
        if retryable:
            # Persistently bad page, not a dead credential — the scan layer
            # degrades this to a shortfall.
            raise PageFetchError(f"{self._platform} call failed ({last}): {endpoint_id}")
        raise FetchError(f"{self._platform} call failed ({last}): {endpoint_id}")

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

        The caller passes the expanded matrix variants; they are OR-joined
        into one query per topic (X ANDs bare terms). The window comes from
        the sampling controller and widens on its L2 — cache keys include it,
        so a widened window re-fetches instead of serving stale pages.
        """
        endpoint_id = TREG_ENDPOINT.get(platform)
        if endpoint_id is None:
            raise FetchError(f"unknown indexed platform: {platform}")
        window_start = window_end - timedelta(days=window_days)
        query = " OR ".join(queries)
        body: dict = {"query": query}
        if platform == "x-indexed":
            body["limit"] = TREG_X_PAGE_LIMIT
            body["query"] = f"{query} since:{window_start} until:{window_end}"
        else:
            body["datePosted"] = linkedin_date_posted(window_days)

        posts: list[FetchedPost] = []
        seen: set[str] = set()
        mapper = _map_x_row if platform == "x-indexed" else _map_linkedin_row
        call_successful_pages = 0
        for page in range(1, TREG_MAX_PAGES + 1):
            try:
                envelope = self._post_cached(
                    endpoint_id,
                    platform,
                    body,
                    [
                        "treg",
                        platform,
                        topic_id,
                        body["query"],
                        str(window_days),
                        window_start.isoformat(),
                        window_end.isoformat(),
                        str(page),
                    ],
                )
                rows, cursor = self._parse_envelope(envelope, endpoint_id, platform)
            except PageFetchError as error:
                if not call_successful_pages:
                    # Nothing banked yet — a leading failure is a dead
                    # platform (bad token, total outage): fail loudly as
                    # today, never return an all-empty batch.
                    raise
                # One persistently bad page after progress: stop paginating
                # this query, keep everything its earlier pages already
                # contributed, record the shortfall, and let the scan
                # continue with other topics.
                self._note_page_failure(endpoint_id)
                self._note_query_failure(endpoint_id)
                logger.warning(
                    "%s topic %r page %d failed after retries: %s — keeping %d posts "
                    "fetched so far and continuing the scan",
                    platform,
                    topic_id,
                    page,
                    error,
                    len(posts),
                )
                break
            self._note_page_success(endpoint_id)
            call_successful_pages += 1
            for row in rows:
                post = mapper(row)
                if post.url not in seen:
                    seen.add(post.url)
                    posts.append(post)
            self._pacing_pause(page == 1)
            if not cursor:
                break
            body = {**body, "cursor": cursor}
        return posts

    def _parse_envelope(
        self, envelope: dict, endpoint_id: str, platform: str
    ) -> tuple[list[dict], str | None]:
        """Validate the pinned treg envelope; malformed shapes fail loudly with the path."""
        output = envelope.get("output")
        if not isinstance(output, dict):
            raise FetchError(f"treg {endpoint_id} envelope malformed at output: expected an object")
        found = output.get("found")
        if not isinstance(found, bool):
            raise FetchError(
                f"treg {endpoint_id} envelope malformed at output.found: expected a boolean"
            )
        data = output.get("data")
        if not isinstance(data, dict):
            raise FetchError(
                f"treg {endpoint_id} envelope malformed at output.data: expected an object"
            )
        if not found:
            # An explicit found=false is an honest empty result, not a shape error.
            logger.info("treg %s found no posts for this query", endpoint_id)
            return [], None
        rows_key = TREG_ROWS_KEY[platform]
        rows = data.get(rows_key)
        if not isinstance(rows, list):
            raise FetchError(
                f"treg {endpoint_id} envelope malformed at output.data.{rows_key}: expected a list"
            )
        numeric = TREG_NUMERIC_KEYS[platform]
        for index, row in enumerate(rows):
            where = f"output.data.{rows_key}[{index}]"
            if not isinstance(row, dict):
                raise FetchError(
                    f"treg {endpoint_id} row {index} malformed at {where}: expected an object"
                )
            missing = [key for key in TREG_ROW_KEYS[platform] if key not in row]
            if missing:
                raise FetchError(
                    f"treg {endpoint_id} row {index} malformed at {where}: missing keys {missing}"
                )
            for key in numeric:
                if isinstance(row[key], bool) or not isinstance(row[key], (int, float)):
                    raise FetchError(
                        f"treg {endpoint_id} row {index} malformed at {where}.{key}: "
                        "expected a number"
                    )
        cursor = data.get("nextCursor")
        if cursor is not None and not isinstance(cursor, str):
            raise FetchError(
                f"treg {endpoint_id} envelope malformed at output.data.nextCursor: "
                "expected a string or null"
            )
        return rows, cursor


def linkedin_date_posted(window_days: int) -> str:
    """Map the sampling window onto LinkedIn's ``datePosted`` enum."""
    if window_days <= 1:
        return "last-day"
    if window_days <= 7:
        return "last-week"
    return "last-month"


def _iso_date(created_utc: float) -> str:
    return datetime.fromtimestamp(created_utc, tz=UTC).date().isoformat()


def _map_x_row(row: dict) -> FetchedPost:
    """One anyapi.x.search.posts row -> the indexed-post shape; engagement = likes+RTs+views."""
    return FetchedPost(
        platform="x-indexed",
        author=str(row["authorUsername"]),
        url=str(row["url"]),
        post_date=_iso_date(row["createdUtc"]),
        snippet=_truncate(str(row["text"])),
        engagement=float(row["likeCount"]) + float(row["retweetCount"]) + float(row["viewCount"]),
    )


def _map_linkedin_row(row: dict) -> FetchedPost:
    """One anyapi.linkedin.search.posts row -> indexed shape; engagement = reactions+comments."""
    return FetchedPost(
        platform="linkedin-indexed",
        author=str(row["authorName"]),
        url=str(row["url"]),
        post_date=_iso_date(row["createdUtc"]),
        snippet=_truncate(str(row["text"])),
        engagement=float(row["reactionCount"]) + float(row["commentCount"]),
    )
