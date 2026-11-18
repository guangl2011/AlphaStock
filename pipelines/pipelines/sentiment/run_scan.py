"""Scan pipeline: expand the matrix, fetch, score, and stage a run.

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
                    "confidence": 0.53, "as_of": "..."}]}
      ]
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
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from .fetchers import FetchedPost, FetchError, RedditFetcher, YouComFetcher
from .matrix import (
    PLATFORMS,
    Matrix,
    MatrixError,
    Topic,
    expand_reddit_queries,
    expand_web_queries,
    load_matrix,
)
from .score import score_text

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


def topic_confidence(post_count: int, distinct_authors: int) -> float:
    """Confidence in [0, 1]: item count and author spread, each saturating."""
    count_component = min(1.0, post_count / CONFIDENCE_COUNT_SATURATION)
    spread_component = min(1.0, distinct_authors / CONFIDENCE_SPREAD_SATURATION)
    return round(0.5 * count_component + 0.5 * spread_component, 3)


def build_topic_block(topic: Topic, posts: list[tuple[FetchedPost, float]], as_of: str) -> dict:
    """One topic's staged block: aggregates plus scored post rows. Never drops an empty topic."""
    scores = [score for _, score in posts]
    topic_score = round(sum(scores) / len(scores), 4) if scores else 0.0
    authors = {post.author for post, _ in posts}
    confidence = topic_confidence(len(posts), len(authors))
    platform_counts = {platform: 0 for platform in PLATFORMS}
    for post, _ in posts:
        platform_counts[post.platform] += 1
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
                "platform": post.platform,
                "author": post.author,
                "url": post.url,
                "post_date": post.post_date,
                "snippet": post.snippet,
                "raw_score": score,
                "topic_score": topic_score,
                "confidence": confidence,
                "as_of": as_of,
            }
            for post, score in posts
        ],
    }


def run_scan(
    matrix: Matrix,
    options: ScanOptions,
    *,
    reddit: RedditFetcher | None = None,
    youcom: YouComFetcher | None = None,
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
    reddit_fetcher = reddit or RedditFetcher(options.cache_dir / "reddit")
    youcom_fetcher = youcom  # constructed lazily — a reddit-only run needs no YDC_API_KEY

    topic_blocks: list[dict] = []
    for topic in topics:
        posts: list[tuple[FetchedPost, float]] = []
        if "reddit" in options.platforms:
            for fetched in reddit_fetcher.fetch(
                topic.topic_id, expand_reddit_queries(topic), window_end
            ):
                posts.append((fetched, score_text(fetched.snippet)))
        if "x-indexed" in options.platforms or "linkedin-indexed" in options.platforms:
            if youcom_fetcher is None:
                youcom_fetcher = YouComFetcher(_ydc_key(), options.cache_dir / "youcom")
            for platform in ("x-indexed", "linkedin-indexed"):
                if platform not in options.platforms:
                    continue
                for fetched in youcom_fetcher.fetch(
                    topic.topic_id, platform, expand_web_queries(topic, platform), window_end
                ):
                    posts.append((fetched, score_text(fetched.snippet)))
        topic_blocks.append(build_topic_block(topic, posts, options.as_of))

    payload = {
        "run_id": options.as_of,
        "as_of": options.as_of,
        "platforms": list(options.platforms),
        "matrix_version": matrix.version,
        "topics": topic_blocks,
    }
    return _stage(payload, options.out_dir), payload


def _ydc_key() -> str:
    return os.environ.get("YDC_API_KEY", "")


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
    print(f"staged {run_path} — {total} posts across {len(payload['topics'])} topics")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
