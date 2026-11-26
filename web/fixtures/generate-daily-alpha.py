"""One-off generator for web/fixtures/daily-alpha.json.

Builds the staged-brief slice from the real signals.json rows so every brief
item is a canonical signal row that parses through toPublicSignal. The shape
mirrors pipelines/agents/daily_alpha.py's brief.json (DailyAlphaDraft
serialized): as_of, bilingual title/takeaway/macro, items, theme_heat, scores.

Run from the repo root: python3 web/fixtures/generate-daily-alpha.py
"""

import json
from collections import Counter
from pathlib import Path

WEB = Path(__file__).resolve().parent
MIN_STRENGTH = 3
MAX_ITEMS = 5

ROWS = json.loads((WEB / "signals.json").read_text(encoding="utf-8"))


def build_brief(as_of: str, window_day: str, score_book: dict[str, tuple[float, float]]) -> dict:
    """Brief for the signals published on ``window_day`` — the writer's
    selection rule (strength ≥ MIN_STRENGTH, strength-desc then id-asc)."""
    candidates = [
        r
        for r in ROWS
        if r["published_at"].startswith(window_day) and r["strength"] >= MIN_STRENGTH
    ]
    candidates.sort(key=lambda r: (-r["strength"], r["signal_id"]))
    chosen = candidates[:MAX_ITEMS]
    theme_heat = sorted(Counter(r["theme"] for r in chosen).items(), key=lambda kv: (-kv[1], kv[0]))
    lead_theme, lead_count = theme_heat[0]

    high_rate = sum(
        1
        for r in chosen
        if (r.get("macro_overlay") or {}).get("rate_sensitivity") == "high"
    )
    rate_part_en = f"{high_rate} of {len(chosen)} items carry high rate sensitivity"
    rate_part_zh = f"{len(chosen)} 条中 {high_rate} 条为高利率敏感"
    if high_rate * 2 > len(chosen):
        macro_en = f"Rate-sensitive names dominate today's brief: {rate_part_en} — the digest reads best in a falling-rate tape."
        macro_zh = f"今日简报以利率敏感信号为主（{rate_part_zh}），利率下行环境对这组信号更为友好。"
    else:
        macro_en = f"Rate sensitivity is mixed in today's brief ({rate_part_en}); no single macro tilt dominates."
        macro_zh = f"今日简报的利率敏感度分布均衡（{rate_part_zh}），没有单一宏观倾向占主导。"

    return {
        "as_of": as_of,
        "title_en": f"Daily Alpha · {window_day}",
        "title_zh": f"每日信号 · {window_day}",
        "takeaway_en": (
            f"{len(chosen)} signals cleared the bar today, led by {lead_theme} ({lead_count})."
        ),
        "takeaway_zh": (
            f"今日 {len(chosen)} 条信号过线，以「{lead_theme}」为主（{lead_count} 条）。"
        ),
        "items": chosen,
        "theme_heat": theme_heat,
        "macro_en": macro_en,
        "macro_zh": macro_zh,
        "scores": {
            ticker: {
                "ticker": ticker,
                "score": now,
                "previous": prev,
                "delta": round(now - prev, 1),
            }
            for ticker, (now, prev) in score_book.items()
            if any(ticker in r["tickers"] for r in chosen)
        },
    }


briefs = [
    build_brief(
        "2026-11-14T21:35:00Z",
        "2026-11-14",
        {"EQIX": (71.2, 69.8), "DLR": (66.4, 66.4), "VRT": (58.9, 60.3)},
    ),
    build_brief(
        "2026-11-08T14:15:00Z",
        "2026-11-08",
        {"EQIX": (69.8, 69.8), "GEV": (63.1, 61.5), "PWR": (60.7, 59.9), "ETN": (57.3, 58.1)},
    ),
]

out = WEB / "daily-alpha.json"
out.write_text(json.dumps({"briefs": briefs}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"wrote {out}: {[(b['title_en'], len(b['items'])) for b in briefs]}")
