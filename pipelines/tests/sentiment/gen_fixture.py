"""One-off generator for web/fixtures/sentiment.json (demo data for fixture mode).

Rows are shaped exactly like PostgREST responses for the `sentiment_runs` and
`sentiment_posts` tables. Scores are produced by the real deterministic
scorer and the real confidence model so the fixture is internally consistent
with what pipelines.sentiment.run_scan stages — demo, not fake math.

Run from pipelines/: python -m tests.sentiment.gen_fixture
"""

# ruff: noqa: E501  (one-off, data-heavy generator; post tuples read badly wrapped)

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipelines.sentiment.run_scan import build_topic_block, topic_confidence  # noqa: E402
from pipelines.sentiment.score import score_text  # noqa: E402

PREV_AS_OF = "2026-11-11T12:00:00Z"
LATEST_AS_OF = "2026-11-18T12:00:00Z"

# (topic_id, label_en, label_zh) in matrix order.
TOPICS = [
    ("dc-capex", "Data center capex & buildout", "数据中心资本开支与建设"),
    ("ai-power-demand", "AI power demand & grid strain", "AI 电力需求与电网压力"),
    ("interconnection-backlog", "Interconnection queue backlogs", "并网排队积压"),
    ("cooling-tech", "Cooling technology (liquid/immersion)", "冷却技术（液冷/浸没式）"),
    ("gpu-supply", "GPU supply & data-center hardware", "GPU 供应与数据中心硬件"),
    ("reit-leasing", "REIT data-center leasing", "REIT 数据中心租赁"),
]

# posts[topic_id] = (previous_count, latest_posts); latest_posts are
# (platform, author, url, post_date, snippet) tuples as a fetch would stage them.
POSTS = {
    "dc-capex": (
        7,
        [
            ("reddit", "u/gridnerd_88", "https://www.reddit.com/r/datacenter/comments/1q8xk2/hyperscaler_capex_guides_up_again/", "2026-11-16", "Hyperscaler capex guides up again — record backlog and fully booked capacity through 2028."),
            ("reddit", "u/liquidcooled", "https://www.reddit.com/r/AI_Infrastructure/comments/1q92mf/delta_forge_leases_take_or_pay/", "2026-11-17", "Another take-or-pay lease signed before the shells are even powered. Demand is not slowing down."),
            ("reddit", "u/index_sam", "https://www.reddit.com/r/stocks/comments/1q97ts/dc_buildout_capex_2027/", "2026-11-17", "Capex guidance keeps getting raised across the hyperscalers. Bullish for the builders, cramped for anyone waiting on GPUs."),
            ("reddit", "u/permitwatch", "https://www.reddit.com/r/datacenter/comments/1q9f0a/transformer_lead_times_still_eating_margins/", "2026-11-18", "Transformer lead times are still eating margins on new builds; some projects delayed into late 2027."),
            ("x-indexed", "gridstatus_watch", "https://x.com/gridstatus_watch/status/1788435192201", "2026-11-16", "Hyperscaler 2026 capex consensus near $800B, up ~50% since January. The constraint is manufacturing, not orders."),
        ],
    ),
    "ai-power-demand": (
        2,
        [
            ("reddit", "u/voltage_diaries", "https://www.reddit.com/r/energy/comments/1q8zf1/data_center_load_growth_outpacing_forecasts/", "2026-11-16", "Data center load growth is outpacing every utility forecast from 2024. Grid strain is showing up in real-time pricing."),
            ("reddit", "u/oh_valley", "https://www.reddit.com/r/AI_Infrastructure/comments/1q93gb/tariff_design_shifts_stranded_cost_risk/", "2026-11-17", "New data-center tariffs shift stranded-cost risk onto the rate class. Strong earnings quality for utilities with signed load."),
            ("reddit", "u/kwh_karen", "https://www.reddit.com/r/investing/comments/1q99lc/who_pays_for_the_wires/", "2026-11-17", "Regulators finally asking who pays for the wires. Slow approval cycles are a real headwind for energization timelines."),
            ("linkedin-indexed", "M&A Advisory Group", "https://www.linkedin.com/posts/mna-advisory_power-demand-activity-7123456789", "2026-11-15", "AI power demand is the single strongest demand signal we have seen in a decade of infrastructure investing."),
        ],
    ),
    "interconnection-backlog": (
        4,
        [
            ("reddit", "u/queuewonk", "https://www.reddit.com/r/energy/comments/1q8fd2/pjm_cycle_2026_results/", "2026-11-16", "PJM's first post-reform cycle: 811 projects, ~220 GW. Reform arrived, completions did not — the backlog is still stalled."),
            ("reddit", "u/wiresup", "https://www.reddit.com/r/utilities/comments/1q90aa/interconnection_wait_times_worse/", "2026-11-16", "Interconnection wait times got worse again this quarter. withdrawn projects keep climbing."),
            ("reddit", "u/transco_ted", "https://www.reddit.com/r/investing/comments/1q96eq/grid_equipment_backlog/", "2026-11-17", "Grid equipment makers have a record backlog. Every stalled project still needs transformers eventually — strong demand."),
        ],
    ),
    "cooling-tech": (
        3,
        [
            ("reddit", "u/hotaisle", "https://www.reddit.com/r/datacenter/comments/1q88ff/liquid_cooling_retrofits/", "2026-11-16", "Liquid cooling retrofits are becoming mandatory for the new accelerator racks. Cooling vendors are fully booked."),
            ("reddit", "u/immersion_ian", "https://www.reddit.com/r/homelab/comments/1q91cl/immersion_cooling_gpu_density/", "2026-11-17", "Immersion cooling keeps winning at extreme GPU density. Expansion across all three of our colo sites."),
            ("x-indexed", "thermal_en", "https://x.com/thermal_en/status/1788440120330", "2026-11-17", "Data center cooling capacity is the hidden bottleneck of the buildout. Orders surged; lead times doubled."),
        ],
    ),
    "gpu-supply": (
        3,
        [
            ("reddit", "u/fab_follower", "https://www.reddit.com/r/hardware/comments/1q89ga/advanced_packaging_capacity_sold_out/", "2026-11-16", "Advanced packaging capacity is sold out well into 2027. CoWoS expansion can't keep up with demand."),
            ("reddit", "u/silicon_sam", "https://www.reddit.com/r/AI_Infrastructure/comments/1q94jj/hbm_supply_tight/", "2026-11-17", "HBM supply stays tight through next year. Memory makers raised guidance again — beats across the board."),
            ("reddit", "u/rack_rat", "https://www.reddit.com/r/stocks/comments/1q98pp/gpu_delivery_slips/", "2026-11-18", "GPU deliveries slipped a quarter for one hyperscaler; shortages are easing slower than the market hoped."),
        ],
    ),
    "reit-leasing": (1, []),
}


def rows_for(as_of: str, which: int) -> tuple[list[dict], list[dict]]:
    run_rows: list[dict] = []
    post_rows: list[dict] = []
    run_id = f"run-{as_of}"
    for topic_id, label_en, label_zh in TOPICS:
        prev_count, latest_posts = POSTS[topic_id]
        if which == 0:  # previous run: summary rows only (posts table omitted for brevity)
            count = prev_count
            authors = max(1, count // 3)
            score = {t[0]: 0.1136 for t in TOPICS}[topic_id] if count else 0.0
            confidence = topic_confidence(count, authors)
            platform_counts = {"reddit": count, "x-indexed": 0, "linkedin-indexed": 0}
        else:
            scored = [(p, score_text(p[4])) for p in latest_posts]
            block = build_topic_block(
                type("T", (), {"topic_id": topic_id, "label_en": label_en, "label_zh": label_zh})(),
                [(type("P", (), {"platform": p[0], "author": p[1], "url": p[2], "post_date": p[3], "snippet": p[4]})(), s) for p, s in scored],
                as_of,
            )
            count = block["post_count"]
            score = block["topic_score"]
            confidence = block["confidence"]
            platform_counts = block["platform_counts"]
            for row in block["posts"]:
                post_rows.append({"run_id": run_id, **row})
        run_rows.append(
            {
                "run_id": run_id,
                "as_of": as_of,
                "topic_id": topic_id,
                "label_en": label_en,
                "label_zh": label_zh,
                "post_count": count,
                "topic_score": score,
                "confidence": confidence,
                "platform_counts": platform_counts,
            }
        )
    return run_rows, post_rows


def main() -> int:
    prev_runs, _ = rows_for(PREV_AS_OF, 0)
    latest_runs, latest_posts = rows_for(LATEST_AS_OF, 1)
    # The previous run's posts table would carry its own rows in production; the
    # page only reads the latest run's feedback list, so the fixture keeps
    # previous-run counts in sentiment_runs and no previous-run post rows.
    fixture = {"runs": [*prev_runs, *latest_runs], "posts": latest_posts}
    out = Path(__file__).resolve().parents[3] / "web" / "fixtures" / "sentiment.json"
    out.write_text(json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} with {len(fixture['runs'])} run rows, {len(fixture['posts'])} post rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
