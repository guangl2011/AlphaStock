"""Fetchers for the Market Sentiment scan: Reddit native, You.com-indexed X/LinkedIn.

* Reddit requires application-only OAuth2 (client_credentials grant):
  unauthenticated public search answers HTTP 403 from datacenter IPs
  (verified on GitHub Actions runners). The fetcher exchanges
  ``REDDIT_CLIENT_ID:REDDIT_CLIENT_SECRET`` for a Bearer token at
  https://www.reddit.com/api/v1/access_token, sends ``Authorization: Bearer``
  to the https://oauth.reddit.com search endpoints, and caches the token
  in-process until ~1 minute before expiry (re-fetching once on a 401).
  Reddit's rate limits still apply under OAuth; requests are paced and
  HTTP 429 answers with exponential time-based backoff. Local anonymous
  development is possible only via the explicit ``REDDIT_ALLOW_ANONYMOUS=1``
  escape hatch.
* There is no free general-purpose API for X or LinkedIn. Both are retrieved
  through the treg.to gateway (``TREG_TOKEN``): a POST to
  ``https://treg.to/call/{endpoint_id}`` authenticates with the
  ``X-Treg-Token`` header — never ``Authorization`` — and answers an envelope
  of ``{provider, resultId, replayed, costUsd, output: {found, data}}``.
  X posts come from ``anyapi.x.search.posts`` (advanced-search syntax, window
  via ``since:/until:`` in the query), LinkedIn posts from
  ``anyapi.linkedin.search.posts`` (window via the ``datePosted`` enum).
  Coverage is only what those indexes expose, and every output row says so
  via its platform label. An optional ``TREG_MAX_COST`` is forwarded as
  ``X-Treg-Route-Max-Cost`` for spend capping.
* Both fetchers are resumable at exact query granularity: Reddit pages carry
  the page number and window tier in the cache key, treg pages carry the
  page number and window, so a widened window or an interrupted run within
  the same window resumes cheaply without re-fetching cached responses.
* Credentials are constructor arguments or environment variables only —
  never logged, cached, or written to any output (the Bearer token lives
  in process memory only).

Tests inject a ``Transport`` and a sleep function, so no test touches the
network or waits on real pacing.
"""

from __future__ import annotations

import base64
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

REDDIT_SEARCH_URL = "https://www.reddit.com/r/{subreddit}/search.json"
REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_USER_AGENT = (
    "alphastock-sentiment/1.0 (internal research scan; github.com/guangl2011/AlphaStock)"
)
REDDIT_CLIENT_ID_ENV = "REDDIT_CLIENT_ID"
REDDIT_CLIENT_SECRET_ENV = "REDDIT_CLIENT_SECRET"
REDDIT_ALLOW_ANONYMOUS_ENV = "REDDIT_ALLOW_ANONYMOUS"
# OAuth endpoints answer 403 to anonymous datacenter-IP traffic; the public
# host stays available to the explicit anonymous escape hatch only.
OAUTH_SEARCH_URL = "https://oauth.reddit.com/r/{subreddit}/search.json"
# Reuse the cached Bearer token until this far from expiry, then re-fetch.
TOKEN_EXPIRY_MARGIN_SECONDS = 60.0
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

DEFAULT_PACE_SECONDS = 1.5
DEFAULT_BACKOFF_BASE_SECONDS = 5.0
DEFAULT_MAX_RETRIES = 3
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

# Sampling v2: Reddit's API max is 100 per request; the controller paginates
# through the ``after`` cursor up to 3 pages per (subreddit, query).
REDDIT_PAGE_LIMIT = 100
REDDIT_MAX_PAGES = 3

SNIPPET_MAX_CHARS = 280


class FetchError(RuntimeError):
    """Raised when a platform cannot be reached or answers unrecoverably."""


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
    """Native Reddit search per (subreddit, query) over application-only OAuth.

    Anonymous public search answers HTTP 403 from datacenter IPs, so the
    fetcher exchanges ``REDDIT_CLIENT_ID:REDDIT_CLIENT_SECRET`` (constructor
    args, falling back to the environment) for a Bearer token and hits the
    ``oauth.reddit.com`` endpoints. Credentials are resolved at construction:
    a missing pair is a loud ``FetchError`` naming the env vars and where to
    set them — never a silent anonymous retry. The only anonymous path is the
    explicit ``REDDIT_ALLOW_ANONYMOUS=1`` local-dev escape hatch. The token
    is cached in-process until ``TOKEN_EXPIRY_MARGIN_SECONDS`` before expiry
    and re-fetched once on a 401 (expired/revoked token).
    """

    def __init__(
        self,
        cache_dir: Path | str,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        allow_anonymous: bool | None = None,
        pace_seconds: float = DEFAULT_PACE_SECONDS,
        backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
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
        self._clock = clock
        self._token: str | None = None
        self._token_expires_at = 0.0
        if allow_anonymous is None:
            allow_anonymous = os.environ.get(REDDIT_ALLOW_ANONYMOUS_ENV) == "1"
        self._anonymous = allow_anonymous
        if self._anonymous:
            self._client_id = self._client_secret = None
            return
        self._client_id = client_id or os.environ.get(REDDIT_CLIENT_ID_ENV) or ""
        self._client_secret = client_secret or os.environ.get(REDDIT_CLIENT_SECRET_ENV) or ""
        if not self._client_id or not self._client_secret:
            raise FetchError(
                "Reddit OAuth credentials are missing — anonymous search requests get "
                "HTTP 403 from datacenter IPs (including GitHub Actions runners). Set "
                f"{REDDIT_CLIENT_ID_ENV} and {REDDIT_CLIENT_SECRET_ENV}: create a Reddit "
                "app at https://www.reddit.com/prefs/apps (choose 'web app'), then add "
                "both values as repository secrets in GitHub (Settings → Secrets and "
                "variables → Actions → New repository secret) for the sentiment-smoke "
                "workflow, or export them in your shell for local runs. To run "
                "anonymous on purpose (rate-limited and likely 403 from datacenter "
                f"IPs), set {REDDIT_ALLOW_ANONYMOUS_ENV}=1 explicitly."
            )

    def _bearer_token(self) -> str:
        """Application-only OAuth token, cached in-process until near expiry."""
        if self._token is not None and self._clock() < (
            self._token_expires_at - TOKEN_EXPIRY_MARGIN_SECONDS
        ):
            return self._token
        assert self._client_id and self._client_secret  # resolved at construction
        basic = base64.b64encode(
            f"{self._client_id}:{self._client_secret}".encode()
        ).decode("ascii")
        status, body = self._transport.post(
            REDDIT_TOKEN_URL,
            headers={
                "Authorization": f"Basic {basic}",
                "User-Agent": REDDIT_USER_AGENT,
                "Accept": "application/json",
            },
            body="grant_type=client_credentials",
            content_type="application/x-www-form-urlencoded",
        )
        if status != 200:
            raise FetchError(f"reddit token fetch failed (HTTP {status}): cannot search")
        try:
            payload = json.loads(body)
            token = payload["access_token"]
            expires_in = float(payload["expires_in"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise FetchError(f"reddit token response is malformed: {error}") from error
        # Secret never leaves this object; the token lives in process memory only.
        self._token = str(token)
        self._token_expires_at = self._clock() + expires_in
        return self._token

    def _search_headers(self) -> dict[str, str]:
        headers = {"User-Agent": REDDIT_USER_AGENT, "Accept": "application/json"}
        if not self._anonymous:
            headers["Authorization"] = f"Bearer {self._bearer_token()}"
        return headers

    def _invalidate_token(self) -> None:
        self._token = None
        self._token_expires_at = 0.0

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
        search_url_template = REDDIT_SEARCH_URL if self._anonymous else OAUTH_SEARCH_URL
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
                    search_url_template.format(subreddit=urllib.parse.quote(subreddit))
                    + "?"
                    + urllib.parse.urlencode(params)
                )
                cache_key_parts = [
                    "reddit",
                    topic_id,
                    subreddit,
                    query,
                    t,
                    str(page),
                    window_end.isoformat(),
                ]
                try:
                    body = self._get_cached(url, self._search_headers(), cache_key_parts)
                except FetchError as error:
                    if self._anonymous or "HTTP 401" not in str(error):
                        raise
                    # Token expired or was revoked: force one refresh and retry.
                    self._invalidate_token()
                    logger.warning("reddit returned 401 — refreshing the OAuth token once")
                    body = self._get_cached(url, self._search_headers(), cache_key_parts)
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
                "your shell for local runs. Reddit-only scans (--platforms reddit) do not "
                "need it."
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

    def _post_cached(self, endpoint_id: str, body: dict, cache_key_parts: list[str]) -> dict:
        """Cache-first gateway POST; re-raises persistent retryable failures as FetchError."""
        path = _cache_path(self._cache_dir, cache_key_parts)
        if path.is_file():
            logger.debug("cache hit %s", path)
            return json.loads(path.read_text(encoding="utf-8"))
        url = TREG_CALL_URL.format(endpoint_id=endpoint_id)
        payload = json.dumps(body)
        last = ""
        for attempt in range(self._max_retries + 1):
            if last:  # a retry: exponential backoff after the previous failure
                self._sleep(self._backoff_base * (2 ** (attempt - 1)))
            status, text = self._transport.post(
                url, self._request_headers(), payload, "application/json"
            )
            if status == 200:
                try:
                    envelope = json.loads(text)
                except json.JSONDecodeError as error:
                    raise FetchError(
                        f"treg {endpoint_id} response is malformed (not JSON): {error}"
                    ) from error
                path.write_text(text, encoding="utf-8")
                return envelope
            last = f"HTTP {status}"
            if status not in RETRYABLE_STATUSES:
                break
            logger.warning("%s returned %s (attempt %d)", url, status, attempt + 1)
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
        for page in range(1, TREG_MAX_PAGES + 1):
            envelope = self._post_cached(
                endpoint_id,
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
