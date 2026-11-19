"""Scan pipeline: expand the matrix, fetch, score, and stage a run.

Sampling v2: quotas are enforced by a per-platform sampling controller, not
hoped for from query templates. Each platform runs an escalation ladder over
the whole topic set — base fan-out first, then fallback subreddits, then
window widening 7 → 14 → 30 days, then phrase relaxation — stopping as soon
as the platform's unique-kept target is met. Every response is deduped by
URL + normalized-text SHA-1 before counting, and shortfalls are reported
honestly in the run's ``coverage`` block; samples are never padded with
duplicates or out-of-window posts.

One invocation produces one append-only JSON file under
``pipelines/data/sentiment/runs/`` plus a ``latest.json`` summary. The file
layout per run::

    {
      "run_id": "2026-11-18T12:00:00Z",
      "as_of": "2026-11-18T12:00:00Z",
      "topics": [
        {"topic_id": "...", "label_en": "...", "label_zh": "...",
         "post_count": 4, "topic_score": 0.12, "confidence": 0.53,
         "platform_counts": {"reddit": 3, "x-indexed": 1, "linkedin-indexed": 0},
         "posts": [{"platform": "...", "author": "...", "url": "...",
                    "post_date": "2026-11-17", "snippet": "...",
                    "raw_score": 0.4117, "topic_score": 0.12,
                    "confidence": 0.53, "confidence_band": "medium",
                    "as_of": "..."}]}
      ],
      "coverage": {
        "reddit": {"fetched": 210, "deduped": 132, "kept": 118,
                   "keep_target": 100, "ladder_steps": ["L0"],
                   "effective_window_days": 7,
                   "topics": {"dc-capex": {"fetched": 40, "deduped": 25, "kept": 24},
                              ...}}
      }
    }

Zero-result topics appear with ``posts: []`` — a topic with nothing found is
an honest empty row, never a dropped topic. Existing run files are never
overwritten (append-only by construction). Scoring is deterministic
(pipelines.sentiment.score); ``as_of`` is a parameter, so the whole run is
reproducible for a given set of cached responses.

Usage::

    python -m pipelines.sentiment.run_scan                      # all topics, all platforms
    python -m pipelines.sentiment.run_scan --platforms reddit   # Reddit-only smoke
    python -m pipelines.sentiment.run_scan --topics dc-capex,cooling-tech
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from .fetchers import FetchedPost, FetchError, RedditTregFetcher, TregFetcher
from .matrix import (
    PLATFORMS,
    Matrix,
    MatrixError,
    Topic,
    expand_reddit_queries,
    expand_web_queries,
    load_matrix,
)
from .score import (
    AggregatePost,
    post_confidence,
    post_confidence_band,
    score_text,
    topic_mean,
)

logger = logging.getLogger(__name__)

SENTIMENT_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "sentiment"
RUNS_DIRNAME = "runs"
LATEST_FILENAME = "latest.json"

# Confidence model: half item-count, half author spread, each saturating.
CONFIDENCE_COUNT_SATURATION = 15.0
CONFIDENCE_SPREAD_SATURATION = 3.0


@dataclass(frozen=True)
class ScanOptions:
    platforms: tuple[str, ...]
    topic_ids: tuple[str, ...] | None  # None = every topic in the matrix
    as_of: str
    cache_dir: Path
    out_dir: Path


@dataclass(frozen=True)
class SamplingStep:
    """One rung of the per-platform escalation ladder.

    ``stop_above``: after the step, stop escalating once the platform's kept
    count reaches this threshold — the ladder's trigger table
    (L1 < 120, L2 < 110, L3 < 100) read as stop conditions.
    """

    name: str
    include_fallback: bool
    window_days: int
    relax_phrases: bool
    stop_above: int


LADDER: tuple[SamplingStep, ...] = (
    SamplingStep("L0", include_fallback=False, window_days=7, relax_phrases=False, stop_above=120),
    SamplingStep("L1", include_fallback=True, window_days=7, relax_phrases=False, stop_above=110),
    SamplingStep("L2a", include_fallback=True, window_days=14, relax_phrases=False, stop_above=110),
    SamplingStep("L2b", include_fallback=True, window_days=30, relax_phrases=False, stop_above=100),
    SamplingStep("L3", include_fallback=True, window_days=30, relax_phrases=True, stop_above=100),
)

REDDIT_WINDOW_TIER = {7: "week", 14: "month", 30: "month"}


@dataclass(frozen=True)
class ScoredPost:
    """A fetched post with its v2 scores, ready for staging."""

    post: FetchedPost
    raw_score: float
    confidence: float
    confidence_band: str

    def aggregate(self) -> AggregatePost:
        """The scorer's aggregate view: engagement weights + recency decay."""
        return AggregatePost(
            score=self.raw_score,
            engagement=self.post.engagement,
            engagement_is_proxy=self.post.platform != "reddit",
            post_date=self.post.post_date,
        )


@dataclass
class PlatformCoverage:
    """Per-platform sampling accounting, staged verbatim in the run JSON."""

    fetched: int = 0
    deduped: int = 0
    kept: int = 0
    ladder_steps: list[str] = field(default_factory=list)
    effective_window_days: int = 7
    topics: dict[str, dict[str, int]] = field(default_factory=dict)

    def to_json(self, keep_target: int) -> dict:
        return {
            "fetched": self.fetched,
            "deduped": self.deduped,
            "kept": self.kept,
            "keep_target": keep_target,
            "ladder_steps": self.ladder_steps,
            "effective_window_days": self.effective_window_days,
            "topics": self.topics,
        }


def parse_as_of(raw: str | None) -> str:
    """ISO-8601 UTC timestamp; ``now`` when omitted (the only clock read in the pipeline)."""
    if raw is None:
        return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"--as-of must be an ISO-8601 timestamp, got {raw!r}") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def window_end_for(as_of: str) -> date:
    """The scan window's end date — also part of the fetch cache keys."""
    return date.fromisoformat(as_of[:10])


def dedupe_key(post: FetchedPost) -> tuple[str, str]:
    """URL + normalized-text SHA-1: the same claim on a repost URL is still a duplicate."""
    normalized = " ".join(post.snippet.lower().split())
    return (post.url, hashlib.sha1(normalized.encode("utf-8")).hexdigest())


def topic_confidence(post_count: int, distinct_authors: int) -> float:
    """Confidence in [0, 1]: item count and author spread, each saturating."""
    count_component = min(1.0, post_count / CONFIDENCE_COUNT_SATURATION)
    spread_component = min(1.0, distinct_authors / CONFIDENCE_SPREAD_SATURATION)
    return round(0.5 * count_component + 0.5 * spread_component, 3)


def score_post(post: FetchedPost) -> ScoredPost:
    """Score one post deterministically: raw score, calibrated confidence, band."""
    confidence = post_confidence(post.snippet)
    return ScoredPost(
        post=post,
        raw_score=score_text(post.snippet),
        confidence=confidence,
        confidence_band=post_confidence_band(confidence),
    )


def _blank(snippet: str) -> bool:
    return not snippet.strip()


def sample_platform(
    platform: str,
    topics: list[Topic],
    targets_fetch_raw: int,
    targets_keep_unique: int,
    fetch_one: Callable[[Topic, SamplingStep], list[FetchedPost]],
) -> tuple[dict[str, list[ScoredPost]], PlatformCoverage]:
    """Run the escalation ladder for one platform across all topics.

    Returns each topic's kept scored posts plus the platform's coverage
    accounting. Shortfalls are reported, never padded.
    """
    seen: set[tuple[str, str]] = set()
    kept_by_topic: dict[str, list[ScoredPost]] = {topic.topic_id: [] for topic in topics}
    coverage = PlatformCoverage()

    for step in LADDER:
        coverage.ladder_steps.append(step.name)
        coverage.effective_window_days = step.window_days
        for topic in topics:
            counters = coverage.topics.setdefault(
                topic.topic_id, {"fetched": 0, "deduped": 0, "kept": 0}
            )
            for post in fetch_one(topic, step):
                coverage.fetched += 1
                counters["fetched"] += 1
                key = dedupe_key(post)
                if key in seen or _blank(post.snippet):
                    continue  # duplicate or unscoreable — never padded back in
                seen.add(key)
                coverage.deduped += 1
                counters["deduped"] += 1
                kept_by_topic[topic.topic_id].append(score_post(post))
                coverage.kept += 1
                counters["kept"] += 1
        if coverage.kept >= step.stop_above:
            break

    if coverage.kept < targets_keep_unique:
        logger.warning(
            "%s sampling fell short of the keep target: %d/%d unique kept "
            "(fetched %d, ladder %s) — reporting the shortfall honestly",
            platform,
            coverage.kept,
            targets_keep_unique,
            coverage.fetched,
            ",".join(coverage.ladder_steps),
        )
    return kept_by_topic, coverage


def build_topic_block(topic: Topic, posts: list[ScoredPost], as_of: str) -> dict:
    """One topic's staged block: aggregates plus scored post rows. Never drops an empty topic."""
    aggregates = [scored.aggregate() for scored in posts]
    topic_score = topic_mean(aggregates, date.fromisoformat(as_of[:10]))
    authors = {scored.post.author for scored in posts}
    confidence = topic_confidence(len(posts), len(authors))
    platform_counts = {platform: 0 for platform in PLATFORMS}
    for scored in posts:
        platform_counts[scored.post.platform] += 1
    return {
        "topic_id": topic.topic_id,
        "label_en": topic.label_en,
        "label_zh": topic.label_zh,
        "post_count": len(posts),
        "topic_score": topic_score,
        "confidence": confidence,
        "platform_counts": platform_counts,
        "posts": [
            {
                "topic_id": topic.topic_id,
                "platform": scored.post.platform,
                "author": scored.post.author,
                "url": scored.post.url,
                "post_date": scored.post.post_date,
                "snippet": scored.post.snippet,
                "raw_score": scored.raw_score,
                "topic_score": topic_score,
                "confidence": scored.confidence,
                "confidence_band": scored.confidence_band,
                "as_of": as_of,
            }
            for scored in posts
        ],
    }


def run_scan(
    matrix: Matrix,
    options: ScanOptions,
    *,
    reddit: RedditTregFetcher | None = None,
    treg: TregFetcher | None = None,
) -> tuple[Path, dict]:
    """Execute one scan; returns the run file path and its payload."""
    unknown = [p for p in options.platforms if p not in PLATFORMS]
    if unknown:
        raise ValueError(f"unknown platforms {unknown} (known: {list(PLATFORMS)})")
    topics = (
        [matrix.topic(topic_id) for topic_id in options.topic_ids]
        if options.topic_ids
        else list(matrix.topics)
    )

    window_end = window_end_for(options.as_of)
    reddit_fetcher = reddit  # constructed lazily — a treg-only run needs no Reddit-specific token
    treg_fetcher = treg  # constructed lazily — a reddit-only run needs no TREG_TOKEN
    targets = matrix.sample_targets

    def fetch_reddit(topic: Topic, step: SamplingStep) -> list[FetchedPost]:
        return reddit_fetcher.fetch(  # type: ignore[union-attr]  # constructed before first use
            topic.topic_id,
            expand_reddit_queries(
                topic, include_fallback=step.include_fallback, relax_phrases=step.relax_phrases
            ),
            window_end,
            t=REDDIT_WINDOW_TIER[step.window_days],
        )

    def fetch_web(platform: str) -> Callable[[Topic, SamplingStep], list[FetchedPost]]:
        def fetch(topic: Topic, step: SamplingStep) -> list[FetchedPost]:
            return treg_fetcher.fetch(  # type: ignore[union-attr]  # constructed before first use
                topic.topic_id,
                platform,
                expand_web_queries(topic, platform, relax_phrases=step.relax_phrases),
                window_end,
                window_days=step.window_days,
            )

        return fetch

    blocks: dict[str, dict] = {}
    coverage: dict[str, dict] = {}
    scored_by_platform: dict[str, dict[str, list[ScoredPost]]] = {}

    if "reddit" in options.platforms:
        if reddit_fetcher is None:
            reddit_fetcher = RedditTregFetcher(options.cache_dir / "treg")
        scored, reddit_coverage = sample_platform(
            "reddit", topics, targets.fetch_raw, targets.keep_unique, fetch_reddit
        )
        scored_by_platform["reddit"] = scored
        coverage_json = reddit_coverage.to_json(targets.keep_unique)
        # Site-wide treg search: the subreddit dimension is spent, so reddit
        # coverage reports unique queries and pages fetched instead of
        # (subreddit, query) pairs.
        if isinstance(reddit_fetcher, RedditTregFetcher):
            coverage_json["unique_queries"] = reddit_fetcher.queries_fetched
            coverage_json["pages_fetched"] = reddit_fetcher.pages_fetched
        coverage["reddit"] = coverage_json
    for platform in ("x-indexed", "linkedin-indexed"):
        if platform not in options.platforms:
            continue
        if treg_fetcher is None:
            treg_fetcher = TregFetcher(options.cache_dir / "treg")
        scored, platform_coverage = sample_platform(
            platform, topics, targets.fetch_raw, targets.keep_unique, fetch_web(platform)
        )
        scored_by_platform[platform] = scored
        coverage[platform] = platform_coverage.to_json(targets.keep_unique)

    for topic in topics:
        posts = [
            scored
            for platform in scored_by_platform
            for scored in scored_by_platform[platform][topic.topic_id]
        ]
        blocks[topic.topic_id] = build_topic_block(topic, posts, options.as_of)

    payload = {
        "run_id": options.as_of,
        "as_of": options.as_of,
        "platforms": list(options.platforms),
        "matrix_version": matrix.version,
        "sample_targets": {
            "fetch_raw": targets.fetch_raw,
            "keep_unique": targets.keep_unique,
        },
        "topics": [blocks[topic.topic_id] for topic in topics],
        "coverage": coverage,
    }
    return _stage(payload, options.out_dir), payload


def _stage(payload: dict, out_dir: Path) -> Path:
    runs_dir = out_dir / RUNS_DIRNAME
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_path = runs_dir / f"scan-{payload['run_id'].replace(':', '')}.json"
    if run_path.exists():
        raise FileExistsError(f"run file already exists — output is append-only: {run_path}")
    run_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    latest_path = out_dir / LATEST_FILENAME
    latest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    logger.info("staged %s (latest summary: %s)", run_path, latest_path)
    return run_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Market Sentiment scan-and-stage pipeline")
    parser.add_argument(
        "--platforms",
        default=",".join(PLATFORMS),
        help="comma-separated subset of reddit,x-indexed,linkedin-indexed",
    )
    parser.add_argument("--topics", default=None, help="comma-separated topic ids (default: all)")
    parser.add_argument(
        "--as-of", dest="as_of", default=None, help="ISO-8601 run timestamp (default: now, UTC)"
    )
    parser.add_argument(
        "--out-dir", dest="out_dir", default=str(SENTIMENT_DATA_DIR), help="staging directory"
    )
    parser.add_argument(
        "--cache-dir",
        dest="cache_dir",
        default=None,
        help="fetch cache directory (default: <out-dir>/.cache)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        matrix = load_matrix()
        options = ScanOptions(
            platforms=tuple(p.strip() for p in args.platforms.split(",") if p.strip()),
            topic_ids=tuple(t.strip() for t in args.topics.split(",")) if args.topics else None,
            as_of=parse_as_of(args.as_of),
            cache_dir=Path(args.cache_dir) if args.cache_dir else Path(args.out_dir) / ".cache",
            out_dir=Path(args.out_dir),
        )
        run_path, payload = run_scan(matrix, options)
    except (MatrixError, FetchError, ValueError, FileExistsError) as error:
        print(f"scan failed: {error}", file=sys.stderr)
        return 1

    total = sum(block["post_count"] for block in payload["topics"])
    coverage = " ".join(
        f"{platform} kept={stats['kept']}" for platform, stats in payload["coverage"].items()
    )
    print(f"staged {run_path} — {total} posts across {len(payload['topics'])} topics ({coverage})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
