"""One-off generator for web/fixtures/sentiment.json (demo data for fixture mode).

Rows are shaped exactly like PostgREST responses for the `sentiment_runs` and
`sentiment_posts` tables, plus the v2 `coverage` block from the sampling
controller. Scores, confidences, and bands are produced by the real
deterministic scorer so the fixture is internally consistent with what
pipelines.sentiment.run_scan stages — demo, not fake math.

Sample volumes mirror the v2 sampling design: every platform clears the
100-unique-sample keep target in the latest run, while one topic
(reit-leasing) stays honestly empty and the coverage block records the
ladder steps each platform needed.

Run from pipelines/: python -m tests.sentiment.gen_fixture
"""

# ruff: noqa: E501  (one-off, data-heavy generator; snippets read badly wrapped)

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipelines.sentiment.fetchers import FetchedPost  # noqa: E402
from pipelines.sentiment.matrix import load_matrix  # noqa: E402
from pipelines.sentiment.run_scan import build_topic_block, score_post  # noqa: E402

PREV_AS_OF = "2026-11-11T12:00:00Z"
LATEST_AS_OF = "2026-11-18T12:00:00Z"

# Reddit kept counts per topic (reit-leasing stays empty — honest empty row).
REDDIT_KEPT = {
    "dc-capex": 24,
    "ai-power-demand": 22,
    "interconnection-backlog": 20,
    "cooling-tech": 26,
    "gpu-supply": 26,
    "reit-leasing": 0,
}
X_KEPT = {
    "dc-capex": 22,
    "ai-power-demand": 20,
    "interconnection-backlog": 20,
    "cooling-tech": 21,
    "gpu-supply": 21,
    "reit-leasing": 0,
}
LINKEDIN_KEPT = {
    "dc-capex": 21,
    "ai-power-demand": 20,
    "interconnection-backlog": 20,
    "cooling-tech": 20,
    "gpu-supply": 20,
    "reit-leasing": 0,
}

# Ladder story per platform: reddit clears the target at L0; the indexed
# platforms escalate (fallback variants / wider window) per the controller.
COVERAGE = {
    "reddit": {
        "ladder_steps": ["L0"],
        "effective_window_days": 7,
        "fetched": 142,
        "deduped": 121,
    },
    "x-indexed": {
        "ladder_steps": ["L0", "L1"],
        "effective_window_days": 14,
        "fetched": 131,
        "deduped": 108,
    },
    "linkedin-indexed": {
        "ladder_steps": ["L0", "L1", "L2a"],
        "effective_window_days": 14,
        "fetched": 124,
        "deduped": 104,
    },
}

# Previous-run summary counts (posts table omitted for previous runs, as the
# page only renders the latest window's feedback list).
PREV_COUNTS = {
    "dc-capex": 24,
    "ai-power-demand": 18,
    "interconnection-backlog": 15,
    "cooling-tech": 14,
    "gpu-supply": 16,
    "reit-leasing": 0,
}

# Topic keyword anchors so snippets read on-topic.
KEYWORDS = {
    "dc-capex": ("hyperscaler capex", "data center buildout"),
    "ai-power-demand": ("data center load growth", "grid strain"),
    "interconnection-backlog": ("interconnection queue", "grid interconnection backlog"),
    "cooling-tech": ("liquid cooling", "immersion cooling"),
    "gpu-supply": ("GPU supply", "advanced packaging capacity"),
    "reit-leasing": ("data center REIT leasing",),
}

BULL_SENTENCES = (
    "backlog keeps setting records and capacity is fully booked through 2028.",
    "another take-or-pay lease signed before the shell even has power — demand is not slowing down.",
    "guidance raised again; the builders are contracted well past next year.",
    "broke ground on the new campus this week — committed capital, not paper plans.",
    "orders surged and the vendor is fully booked; strong demand signal.",
    "management said contracted revenue covers the expansion — beats across the board.",
)

BEAR_SENTENCES = (
    "the project got delayed again, pushed right into late 2027.",
    "curtailment risk is rising and approvals are a real headwind.",
    "lead times are still eating margins on every new build.",
    "the queue is stalled; withdrawn projects keep climbing and nothing cleared.",
)

NEUTRAL_SENTENCES = (
    "watching this one — mixed reads on whether orders hold through next quarter.",
    "the disclosure was thin; hard to score either way until the next update.",
)

REDDIT_SUBS = {
    "dc-capex": "datacenter",
    "ai-power-demand": "energy",
    "interconnection-backlog": "utilities",
    "cooling-tech": "AI_Infrastructure",
    "gpu-supply": "hardware",
    "reit-leasing": "stocks",
}
REDDIT_AUTHORS = ("gridnerd", "liquidcooled", "index_sam", "permitwatch", "fab_follower", "rack_rat")
X_HANDLES = ("gridstatus_watch", "thermal_en", "dc_builder", "power_ops", "silicon_wonk")
X_DOMAINS = ("x.com", "twitter.com")
LINKEDIN_ORGS = ("mna-advisory", "infra-capital-partners", "grid-asset-note", "digital-realty-watch")


def slugify(keyword: str) -> str:
    return keyword.replace(" ", "-").replace("/", "")


def snippet_for(topic_id: str, i: int) -> str:
    """Deterministic, varied snippet: intro varies by index, body cycles pools."""
    keyword = KEYWORDS[topic_id][i % len(KEYWORDS[topic_id])]
    pool: tuple[str, ...]
    if i % 5 == 3:
        pool = BEAR_SENTENCES
    elif i % 9 == 7:
        pool = NEUTRAL_SENTENCES
    else:
        pool = BULL_SENTENCES
    body = pool[(i // 2) % len(pool)]
    intro = (f"{keyword.capitalize()} thread #{i + 1}:", f"Update on {keyword}:", f"Re: {keyword} —")[i % 3]
    return f"{intro} {body}"


def posts_for(topic_id: str, platform: str, kept: int, start_index: int) -> list[FetchedPost]:
    """Deterministic unique posts: index-derived authors, URLs, and dates."""
    posts = []
    for i in range(kept):
        n = start_index + i
        snippet = snippet_for(topic_id, n)
        day = 12 + (n % 7)  # 2026-11-12 .. 2026-11-18, inside even a 7-day window
        post_date = f"2026-11-{day:02d}"
        if platform == "reddit":
            author = f"u/{REDDIT_AUTHORS[n % len(REDDIT_AUTHORS)]}_{n:02d}"
            url = (
                f"https://www.reddit.com/r/{REDDIT_SUBS[topic_id]}/comments/"
                f"1q{n:04d}{slugify(KEYWORDS[topic_id][0])}/"
            )
        elif platform == "x-indexed":
            handle = X_HANDLES[n % len(X_HANDLES)]
            author = handle
            url = f"https://{X_DOMAINS[n % 2]}/{handle}/status/17884{n:05d}"
        else:
            org = LINKEDIN_ORGS[n % len(LINKEDIN_ORGS)]
            author = org.replace("-", " ").title()
            url = f"https://www.linkedin.com/posts/{org}_{slugify(KEYWORDS[topic_id][0])}-activity-7123456{n:03d}"
        posts.append(
            FetchedPost(
                platform=platform,
                author=author,
                url=url,
                post_date=post_date,
                snippet=snippet,
                engagement=0.0 if platform != "reddit" else float(3 + (n % 40) + (n % 12)),
            )
        )
    return posts


def latest_rows(matrix) -> tuple[list[dict], list[dict], dict]:
    """Latest-run rows from the real scorer, plus the coverage block."""
    run_rows: list[dict] = []
    post_rows: list[dict] = []
    per_platform_kept = {p: 0 for p in COVERAGE}
    coverage_topics: dict[str, dict[str, dict]] = {p: {} for p in COVERAGE}

    for topic in matrix.topics:
        scored = []
        for platform, _kept_map, kept_total in (
            ("reddit", REDDIT_KEPT, REDDIT_KEPT[topic.topic_id]),
            ("x-indexed", X_KEPT, X_KEPT[topic.topic_id]),
            ("linkedin-indexed", LINKEDIN_KEPT, LINKEDIN_KEPT[topic.topic_id]),
        ):
            fetched = posts_for(topic.topic_id, platform, kept_total, start_index=per_platform_kept[platform])
            per_platform_kept[platform] += kept_total
            scored.extend(score_post(p) for p in fetched)
            # Platform-level fetched/deduped margins get spread across topics
            # proportionally after the topic loop (below).
            coverage_topics[platform][topic.topic_id] = {
                "fetched": kept_total,
                "deduped": kept_total,
                "kept": kept_total,
            }
        block = build_topic_block(topic, scored, LATEST_AS_OF)
        run_rows.append(
            {
                "run_id": f"run-{LATEST_AS_OF}",
                "as_of": LATEST_AS_OF,
                **{k: block[k] for k in ("topic_id", "label_en", "label_zh", "post_count", "topic_score", "confidence", "platform_counts")},
            }
        )
        for row in block["posts"]:
            post_rows.append({"run_id": f"run-{LATEST_AS_OF}", **row})

    coverage = {}
    for platform, reported in COVERAGE.items():
        topics = coverage_topics[platform]
        kept = sum(t["kept"] for t in topics.values())
        deduped = max(kept, reported["deduped"])
        coverage[platform] = {
            **reported,
            "deduped": deduped,
            "kept": kept,
            "keep_target": matrix.sample_targets.keep_unique,
            "topics": topics,
        }
        for entry in topics.values():
            # fetched > deduped: spread the platform's dedupe margin by kept share.
            share = entry["kept"] / max(1, kept)
            entry["deduped"] = entry["kept"] + round((deduped - kept) * share)
            entry["fetched"] = entry["deduped"] + round((reported["fetched"] - deduped) * share)
    return run_rows, post_rows, coverage


def main() -> int:
    matrix = load_matrix()
    prev_counts = PREV_COUNTS
    run_rows, post_rows, coverage = latest_rows(matrix)

    prev_rows = [
        {
            "run_id": f"run-{PREV_AS_OF}",
            "as_of": PREV_AS_OF,
            "topic_id": topic.topic_id,
            "label_en": topic.label_en,
            "label_zh": topic.label_zh,
            "post_count": prev_counts[topic.topic_id],
            "topic_score": 0.1136,
            "confidence": 0.3,
            "platform_counts": {
                "reddit": prev_counts[topic.topic_id],
                "x-indexed": 0,
                "linkedin-indexed": 0,
            },
        }
        for topic in matrix.topics
    ]

    fixture = {
        "runs": [*prev_rows, *run_rows],
        "posts": post_rows,
        "coverage": coverage,
    }
    out = Path(__file__).resolve().parents[3] / "web" / "fixtures" / "sentiment.json"
    out.write_text(json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    kept = {p: c["kept"] for p, c in coverage.items()}
    print(f"wrote {out}: {len(fixture['runs'])} run rows, {len(post_rows)} post rows, kept={kept}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
