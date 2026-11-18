"""Reader and schema validator for the Market Sentiment prompt/keyword matrix.

The matrix (``pipelines/data/sentiment/matrix.json``) is the single declarative
source of truth for what gets scanned, where, and how. Adding a topic or a
keyword is a data edit; this module is the wall between the data and the
pipeline: every row is validated eagerly and an invalid row raises
``MatrixError`` rather than producing a half-configured scan.

Templates are Python ``str.format`` templates with a single ``{keyword}``
placeholder. Reddit searches run natively per subreddit — the subreddit is an
API parameter, not part of the query — so the reddit template expands to the
bare keyword query that each subreddit search receives, while the X and
LinkedIn templates carry the ``site:`` scopes sent through the You.com index.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MATRIX_PATH = Path(__file__).resolve().parents[2] / "data" / "sentiment" / "matrix.json"

TOPIC_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
PLATFORMS: tuple[str, ...] = ("reddit", "x-indexed", "linkedin-indexed")

# Platform fields on each topic row: x and linkedin hold bare You.com templates.
WEB_PLATFORMS: dict[str, str] = {"x-indexed": "x", "linkedin-indexed": "linkedin"}


class MatrixError(ValueError):
    """Raised when the matrix file is missing, malformed, or internally inconsistent."""


@dataclass(frozen=True)
class RedditSpec:
    subreddits: list[str]
    query_template: str


@dataclass(frozen=True)
class Topic:
    topic_id: str
    label_en: str
    label_zh: str
    keywords: list[str]
    tickers: list[str]
    reddit: RedditSpec
    x_query_template: str
    linkedin_query_template: str
    recency_days: int


@dataclass(frozen=True)
class Matrix:
    version: str
    topics: tuple[Topic, ...]

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


def _template(record: dict, key: str, where: str) -> str:
    value = record.get(key)
    _require(isinstance(value, str) and value.strip(), f"{where}.{key} must be a non-empty string")
    _require("{keyword}" in value, f"{where}.{key} must contain a {{keyword}} placeholder")
    return value  # type: ignore[return-value]


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
    reddit = RedditSpec(
        subreddits=_str_list(reddit_raw, "subreddits", f"{where}.reddit"),
        query_template=_template(reddit_raw, "query_template", f"{where}.reddit"),
    )
    _require(
        all(re.match(r"^[A-Za-z0-9_]+$", s) for s in reddit.subreddits),
        f"{where}.reddit.subreddits must be bare subreddit names",
    )

    web_platform_specs: dict[str, str] = {}
    for field in WEB_PLATFORMS.values():
        platform_raw = record.get(field)
        _require(isinstance(platform_raw, dict), f"{where}.{field} must be an object")
        web_platform_specs[field] = _template(platform_raw, "query_template", f"{where}.{field}")

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
        x_query_template=web_platform_specs["x"],
        linkedin_query_template=web_platform_specs["linkedin"],
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

    return Matrix(version=version, topics=topics)


def load_matrix(path: Path | str | None = None) -> Matrix:
    matrix_path = Path(path) if path else DEFAULT_MATRIX_PATH
    if not matrix_path.is_file():
        raise MatrixError(f"matrix file not found: {matrix_path}")
    return parse_matrix(json.loads(matrix_path.read_text(encoding="utf-8")), matrix_path)


def expand_reddit_queries(topic: Topic) -> list[tuple[str, str]]:
    """(subreddit, query) pairs — keyword template per subreddit, in matrix order."""
    return [
        (subreddit, topic.reddit.query_template.format(keyword=keyword))
        for subreddit in topic.reddit.subreddits
        for keyword in topic.keywords
    ]


def expand_web_queries(topic: Topic, platform: str) -> list[str]:
    """You.com query strings for ``x-indexed`` or ``linkedin-indexed``, in matrix order."""
    field = WEB_PLATFORMS.get(platform)
    if field is None:
        raise MatrixError(f"unknown web platform {platform!r} (known: {sorted(WEB_PLATFORMS)})")
    template = topic.x_query_template if field == "x" else topic.linkedin_query_template
    return [template.format(keyword=keyword) for keyword in topic.keywords]
