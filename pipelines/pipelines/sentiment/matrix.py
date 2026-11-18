"""Reader and schema validator for the Market Sentiment prompt/keyword matrix (v2).

The matrix (``pipelines/data/sentiment/matrix.json``) is the single declarative
source of truth for what gets scanned, where, and how. Adding a topic, a
keyword, or a query variant is a data edit; this module is the wall between the
data and the pipeline: every row is validated eagerly and an invalid row raises
``MatrixError`` rather than producing a half-configured scan.

v2.0.0 schema, per topic:

* ``query_variants`` — 3-5 Python ``str.format`` templates per platform, each
  with a ``{keyword}`` placeholder and optionally ``{keyword_or}`` (the next
  keyword in rotation, for unquoted OR-expansion) and ``{ticker}`` (the
  topic's first ticker, for ticker-scoped queries).
* tiered Reddit subreddits — ``subreddits.primary`` and
  ``subreddits.fallback`` lists; the sampling controller escalates to the
  fallback tier when a scan runs thin.
* matrix-level ``sample_targets`` — the per-platform raw-fetch and
  unique-kept quotas the controller enforces.

Reddit searches run natively per subreddit — the subreddit is an API
parameter, not part of the query — so reddit variants carry only the keyword
expression, while the X and LinkedIn variants carry the ``site:`` scopes sent
through the treg.to gateway's X and LinkedIn search endpoints.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MATRIX_PATH = Path(__file__).resolve().parents[2] / "data" / "sentiment" / "matrix.json"

TOPIC_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
PLATFORMS: tuple[str, ...] = ("reddit", "x-indexed", "linkedin-indexed")

# Platform fields on each topic row: x and linkedin hold bare indexed-search variants.
WEB_PLATFORMS: dict[str, str] = {"x-indexed": "x", "linkedin-indexed": "linkedin"}

MIN_VARIANTS, MAX_VARIANTS = 3, 5


class MatrixError(ValueError):
    """Raised when the matrix file is missing, malformed, or internally inconsistent."""


@dataclass(frozen=True)
class SubredditTiers:
    primary: list[str]
    fallback: list[str]

    def all(self) -> list[str]:
        """Primary then fallback, in matrix order (deduped)."""
        return list(dict.fromkeys([*self.primary, *self.fallback]))


@dataclass(frozen=True)
class RedditSpec:
    subreddits: SubredditTiers
    query_variants: list[str]


@dataclass(frozen=True)
class WebSpec:
    query_variants: list[str]


@dataclass(frozen=True)
class SampleTargets:
    fetch_raw: int
    keep_unique: int


@dataclass(frozen=True)
class Topic:
    topic_id: str
    label_en: str
    label_zh: str
    keywords: list[str]
    tickers: list[str]
    reddit: RedditSpec
    x: WebSpec
    linkedin: WebSpec
    recency_days: int

    @property
    def x_query_template(self) -> str:
        """The base (exact-phrase) X template — kept for smoke tooling."""
        return self.x.query_variants[0]

    @property
    def linkedin_query_template(self) -> str:
        return self.linkedin.query_variants[0]


@dataclass(frozen=True)
class Matrix:
    version: str
    topics: tuple[Topic, ...]
    sample_targets: SampleTargets

    def topic(self, topic_id: str) -> Topic:
        """The named topic; unknown ids fail loudly — never a silent None."""
        for candidate in self.topics:
            if candidate.topic_id == topic_id:
                return candidate
        known = ", ".join(t.topic_id for t in self.topics)
        raise MatrixError(f"unknown topic_id {topic_id!r} (known: {known})")

    def topic_ids(self) -> list[str]:
        return [t.topic_id for t in self.topics]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MatrixError(message)


def _str_list(record: dict, key: str, where: str) -> list[str]:
    values = record.get(key)
    _require(
        isinstance(values, list) and len(values) > 0, f"{where}.{key} must be a non-empty list"
    )
    _require(
        all(isinstance(v, str) and v.strip() for v in values),
        f"{where}.{key} must be non-empty strings",
    )
    return values  # type: ignore[return-value]


def _query_variants(record: dict, where: str) -> list[str]:
    """3-5 non-empty templates, each carrying a {keyword} placeholder."""
    values = record.get("query_variants")
    _require(
        isinstance(values, list) and MIN_VARIANTS <= len(values) <= MAX_VARIANTS,
        f"{where}.query_variants must be a list of {MIN_VARIANTS}-{MAX_VARIANTS} templates",
    )
    for value in values:
        _require(
            isinstance(value, str) and value.strip(),
            f"{where}.query_variants must be non-empty strings",
        )
        _require(
            "{keyword}" in value,
            f"{where}.query_variants entries must contain a {{keyword}} placeholder",
        )
    return values  # type: ignore[return-value]


def _sample_targets(raw: dict, where: str) -> SampleTargets:
    targets = raw.get("sample_targets")
    _require(isinstance(targets, dict), f"{where}.sample_targets must be an object")
    fetch_raw, keep_unique = targets.get("fetch_raw"), targets.get("keep_unique")
    _require(
        isinstance(fetch_raw, int) and not isinstance(fetch_raw, bool) and fetch_raw > 0,
        f"{where}.sample_targets.fetch_raw must be a positive integer",
    )
    _require(
        isinstance(keep_unique, int) and not isinstance(keep_unique, bool) and keep_unique > 0,
        f"{where}.sample_targets.keep_unique must be a positive integer",
    )
    _require(
        fetch_raw >= keep_unique,
        f"{where}.sample_targets.fetch_raw must be >= keep_unique (raw fetches feed dedupe)",
    )
    return SampleTargets(fetch_raw=fetch_raw, keep_unique=keep_unique)


def parse_topic(raw: object, where: str) -> Topic:
    _require(isinstance(raw, dict), f"{where} must be an object")
    record: dict = raw
    topic_id = record.get("topic_id")
    _require(
        isinstance(topic_id, str) and TOPIC_ID_RE.match(topic_id),
        f"{where}.topic_id must be a kebab-case slug",
    )

    for key in ("label_en", "label_zh"):
        _require(
            isinstance(record.get(key), str) and record[key].strip(),
            f"{where}.{key} must be a non-empty string",
        )

    keywords = _str_list(record, "keywords", where)
    tickers = _str_list(record, "tickers", where)
    _require(
        all(isinstance(t, str) and t.isupper() and t.isalpha() for t in tickers),
        f"{where}.tickers must be uppercase symbols",
    )

    reddit_raw = record.get("reddit")
    _require(isinstance(reddit_raw, dict), f"{where}.reddit must be an object")
    subreddits_raw = reddit_raw.get("subreddits")
    _require(isinstance(subreddits_raw, dict), f"{where}.reddit.subreddits must be an object")
    tiers = SubredditTiers(
        primary=_str_list(subreddits_raw, "primary", f"{where}.reddit.subreddits"),
        fallback=_str_list(subreddits_raw, "fallback", f"{where}.reddit.subreddits"),
    )
    _require(
        all(re.match(r"^[A-Za-z0-9_]+$", s) for s in tiers.all()),
        f"{where}.reddit.subreddits must be bare subreddit names",
    )
    reddit = RedditSpec(
        subreddits=tiers, query_variants=_query_variants(reddit_raw, f"{where}.reddit")
    )

    web_specs: dict[str, WebSpec] = {}
    for field in WEB_PLATFORMS.values():
        platform_raw = record.get(field)
        _require(isinstance(platform_raw, dict), f"{where}.{field} must be an object")
        web_specs[field] = WebSpec(query_variants=_query_variants(platform_raw, f"{where}.{field}"))

    recency = record.get("recency_days")
    _require(
        isinstance(recency, int) and not isinstance(recency, bool) and 1 <= recency <= 90,
        f"{where}.recency_days must be an integer in [1, 90]",
    )

    return Topic(
        topic_id=topic_id,
        label_en=record["label_en"],
        label_zh=record["label_zh"],
        keywords=keywords,
        tickers=tickers,
        reddit=reddit,
        x=web_specs["x"],
        linkedin=web_specs["linkedin"],
        recency_days=recency,
    )


def parse_matrix(raw: object, source: Path | None = None) -> Matrix:
    """Validate a raw matrix payload; raises MatrixError on any bad row."""
    where = f"matrix ({source})" if source else "matrix"
    _require(isinstance(raw, dict), f"{where} must be an object")
    version = raw.get("version")
    _require(
        isinstance(version, str) and version.strip(), f"{where}.version must be a non-empty string"
    )
    topics_raw = raw.get("topics")
    _require(
        isinstance(topics_raw, list) and len(topics_raw) > 0,
        f"{where}.topics must be a non-empty list",
    )

    topics = tuple(parse_topic(t, f"{where}.topics[{i}]") for i, t in enumerate(topics_raw))
    ids = [t.topic_id for t in topics]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    _require(not duplicates, f"{where} has duplicate topic_ids: {duplicates}")

    return Matrix(version=version, topics=topics, sample_targets=_sample_targets(raw, where))


def load_matrix(path: Path | str | None = None) -> Matrix:
    matrix_path = Path(path) if path else DEFAULT_MATRIX_PATH
    if not matrix_path.is_file():
        raise MatrixError(f"matrix file not found: {matrix_path}")
    return parse_matrix(json.loads(matrix_path.read_text(encoding="utf-8")), matrix_path)


def fill_variant(template: str, keyword: str, keywords: list[str], tickers: list[str]) -> str:
    """Fill one variant template; {keyword_or} cycles to the next keyword, {ticker} to the first."""
    keyword_or = keywords[(keywords.index(keyword) + 1) % len(keywords)]
    return template.format(keyword=keyword, keyword_or=keyword_or, ticker=tickers[0])


def expand_reddit_queries(
    topic: Topic, *, include_fallback: bool = False, relax_phrases: bool = False
) -> list[tuple[str, str]]:
    """(subreddit, query) pairs — subreddit x keyword x variant, in matrix order.

    ``include_fallback`` extends the subreddit pool to the fallback tier (L1);
    ``relax_phrases`` strips quoting so phrase searches become any-term
    searches (L3).
    """
    tiers = topic.reddit.subreddits
    subreddits = tiers.all() if include_fallback else tiers.primary
    queries: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for subreddit in subreddits:
        for keyword in topic.keywords:
            for variant in topic.reddit.query_variants:
                if relax_phrases:
                    variant = variant.replace('"', "")
                query = fill_variant(variant, keyword, topic.keywords, topic.tickers)
                if (subreddit, query) not in seen:
                    seen.add((subreddit, query))
                    queries.append((subreddit, query))
    return queries


def expand_web_queries(
    topic: Topic, platform: str, *, relax_phrases: bool = False
) -> list[str]:
    """Indexed-search query strings for ``x-indexed`` or ``linkedin-indexed``.

    keyword x variant, in matrix order.
    """
    field = WEB_PLATFORMS.get(platform)
    if field is None:
        raise MatrixError(f"unknown web platform {platform!r} (known: {sorted(WEB_PLATFORMS)})")
    spec = topic.x if field == "x" else topic.linkedin
    queries: list[str] = []
    seen: set[str] = set()
    for keyword in topic.keywords:
        for variant in spec.query_variants:
            if relax_phrases:
                variant = variant.replace('"', "")
            query = fill_variant(variant, keyword, topic.keywords, topic.tickers)
            if query not in seen:
                seen.add(query)
                queries.append(query)
    return queries
