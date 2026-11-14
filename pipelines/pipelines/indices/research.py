"""Three-year research frame: AS-100 / AS-MCG100 / AS-MCB100 backfilled from 2024-01-02.

The production indices base at their construction dates (Nov 2026). This
module answers a different, explicitly *research* question: how would the
two universes, as constituted today, have tracked since the start of 2024?
It reuses the production :class:`EqualWeightEngine` unchanged — same equal
weight, same quarterly rebalance calendar, same corporate-action handling —
and adds only what a backfill needs:

* **Base 1000 at the first trading day of the frame** (2024-01-02) for
  both indices, so the two universes and every benchmark share one anchor.
* **Point-in-time entry.** A constituent that was not trading on the base
  date (IPO, spin-off, relisting) enters at the first quarterly rebalance
  on which it has a close — never retroactively. GE Vernova's when-issued
  prints (2024-03-27..04-01) never count: a sourced regular-way listing
  date in ``data/research/listing_dates.csv`` gates eligibility, so GEV
  enters at the 2024-06-28 rebalance.
  Every entry is recorded as a structured event.
* **Documented exclusions stay documented.** Gaps are reconciled through
  ``index_exclusions.csv`` exactly as in production; an undocumented gap
  fails the run. (The former PSTG gap was a ticker change — now ``P``.)
* **Stale closes are loud.** If a held name has no close on a trading day
  (halt), its last close carries forward and the day is logged as an event.

Caveats are carried in every artifact, not footnoted away: this is a
current-constituent backtest, so it has survivorship / look-ahead bias by
construction (today's universe was selected with hindsight), and it is a
price-return series computed from a non-licensed research source. It is an
internal research view — never a published index value (methodology §8).

Run (deterministic, network-free, from the checked-in frame)::

    python -m pipelines.indices.research --web-out ../web/fixtures/research.json

Refresh the frame from Yahoo first (network; needs ``yfinance``)::

    python -m pipelines.indices.research --refresh --end 2026-11-13
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import logging
import math
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .benchmarks import INDEX_BENCHMARKS, PROXY_MAP, rebase_proxy
from .compute import INDEX_IDS, METHODOLOGY_VERSION, UNIVERSE_LABELS, load_compositions
from .dates import rebalance_dates
from .engine import BASE_LEVEL, EngineError, EodRecord, EqualWeightEngine, IndexEvent
from .exclusions import Exclusion, load_exclusions, reconcile

logger = logging.getLogger(__name__)

FRAME_START = date(2024, 1, 1)
TRADING_DAYS_PER_YEAR = 252

_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FRAME = _ROOT / "data" / "research" / "eod_frame.csv.gz"
LISTING_DATES_CSV = _ROOT / "data" / "research" / "listing_dates.csv"
CORP_EVENTS_CSV = _ROOT / "data" / "research" / "corporate_events.csv"

#: Event kinds whose price effect must already be folded into the frame close.
ADJUSTED_KINDS = frozenset({"split", "reverse_split", "spin_off", "special_dividend", "merger"})
#: A larger one-day close move on such an event date means the vendor did not adjust.
UNADJUSTED_MOVE = 0.25
DEFAULT_OUT_DIR = _ROOT / "outputs" / "research"

#: Thematic comparators shown alongside each index's broad benchmark set.
THEMATIC_PROXIES = ("GRID", "ROBO", "URA", "XLU")

CAVEATS = (
    "Current-constituent backtest: all three universes were selected in Nov 2026 with "
    "hindsight, so the 2024-2026 series carries survivorship and look-ahead bias by "
    "construction. Read it as 'how today's names travelled', not as an achievable "
    "track record.",
    "Price return only (ordinary dividends unadjusted), equal weight, quarterly "
    "rebalance on the last trading day of Mar/Jun/Sep/Dec — production methodology "
    "v4.0 mechanics.",
    "Late listings (IPO / spin-off / relisting / de-SPAC after 2024-01-02) enter at the first "
    "quarterly rebalance on/after their sourced regular-way listing date "
    "(data/research/listing_dates.csv); when-issued and pre-listing prints never count.",
    "Research source: Yahoo Finance EOD via yfinance (split-adjusted close). Not a "
    "licensed index-data feed; internal research view only, never a published index "
    "value (methodology §8 publication gate).",
    "Universes are the v4.0 lists checked into this repo: AS-100 from large_cap_100.csv "
    "(2026-11-09 construction) and AS-MCG100 / AS-MCB100 from mixed_cap_universe.csv "
    "(2026-11-13 screen built from You.com discovery and Yahoo caps). Cap bands are "
    "today's, not 2024's, so names that grew into a band are backtested inside it.",
    "Benchmarks are ETF price proxies rebased to 1000 on the same base date; licensed "
    "index values are never fetched.",
    "Corporate events (splits, spin-offs, ticker changes, de-SPAC listings, pending deals) "
    "were researched per constituent with the You.com Search API and curated with sources "
    "in data/research/corporate_events.csv; every split / spin-off is verified to be "
    "adjusted in the price frame on its effective date.",
)


#: Chinese caveats — same order and substance as CAVEATS (the Explorer shows
#: one language at a time; never mix the two lists).
CAVEATS_ZH = (
    "本回测采用当前成分股：三个宇宙均于 2026 年 11 月在已知结果的情况下选出，"
    "因此 2024–2026 年序列天然带有幸存者偏差和前视偏差。请将其理解为“当前这批股票的历史走势”，"
    "而非可实现的业绩记录。",
    "仅计价格收益（普通股息不调整），等权重，于每年 3/6/9/12 月最后一个交易日季度再平衡，"
    "沿用生产方法论 v4.0 的计算机制。",
    "2024-01-02 之后上市的公司（IPO、分拆、重新上市、SPAC 合并）在其有据可查的正式上市日"
    "之后的首个季度再平衡日纳入（见 data/research/listing_dates.csv）；"
    "when-issued 及上市前报价一律不计。",
    "研究数据来源：经 yfinance 获取的 Yahoo Finance 日终数据（经拆股调整的收盘价）。"
    "并非授权指数数据，仅供内部研究，绝不作为对外发布的指数值（方法论第 8 节发布门槛）。",
    "宇宙为仓库中的 v4.0 名单：AS-100 取自 large_cap_100.csv（2026-11-09 构建），"
    "AS-MCG100 / AS-MCB100 取自 mixed_cap_universe.csv（2026-11-13 筛选，基于 You.com 检索与 "
    "Yahoo 市值）。市值区间按当前而非 2024 年划分，因此后来才进入该区间的股票也按该区间回测。",
    "基准为 ETF 价格代理，于同一基期重设为 1000；从不获取授权指数数值。",
    "公司行动（拆股、分拆、代码变更、SPAC 合并上市、待完成交易）已用 You.com 搜索 API "
    "逐只研究，并附来源整理于 data/research/corporate_events.csv；每次拆股/分拆均核实已在"
    "价格数据中于生效日完成调整。",
)


@dataclass
class ResearchIndex:
    index_id: str
    base_date: date
    levels: dict[date, float]
    counts: dict[date, int]
    constituents: list[str]
    initial: list[str]
    entries: list[tuple[date, str]] = field(default_factory=list)
    excluded: list[Exclusion] = field(default_factory=list)
    events: list[IndexEvent] = field(default_factory=list)


# -- input -------------------------------------------------------------------


def parse_frame(text: str) -> dict[str, list[EodRecord]]:
    """Frame CSV text -> EOD records per ticker, sorted by date."""
    records: dict[str, list[EodRecord]] = {}
    for i, row in enumerate(csv.DictReader(io.StringIO(text)), start=2):
        try:
            rec = EodRecord(
                date.fromisoformat(row["date"]), float(row["close"]), float(row["adjClose"])
            )
        except (KeyError, ValueError) as exc:
            raise EngineError(f"research frame row {i}: malformed ({exc})") from exc
        records.setdefault(row["ticker"].strip().upper(), []).append(rec)
    for recs in records.values():
        recs.sort(key=lambda r: r.date)
    return records


def load_listing_dates(path: Path | str = LISTING_DATES_CSV) -> dict[str, date]:
    """Sourced regular-way first-trade dates for names listed after the frame start.

    Vendors file pre-listing prints (e.g. GE Vernova's when-issued "GEV WI")
    under the final ticker; this PR-reviewed table is what decides when a
    name actually became investable. Every row must carry a source.
    """
    path = Path(path)
    if not path.is_file():
        return {}
    out: dict[str, date] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):
            if not row.get("source", "").strip():
                raise EngineError(f"listing_dates.csv line {i}: source is required")
            out[row["ticker"].strip().upper()] = date.fromisoformat(
                row["regular_way_first_trade"].strip()
            )
    return out


_EVENT_COLUMNS = ("ticker", "date", "event", "detail", "yahoo_factor",
                  "index_treatment", "status", "source")


def load_corporate_events(path: Path | str = CORP_EVENTS_CSV) -> list[dict[str, str]]:
    """Curated, sourced corporate events (You.com research, human-reviewed)."""
    path = Path(path)
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = [c for c in _EVENT_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise EngineError(f"corporate_events.csv missing column(s): {missing}")
        rows = []
        for i, row in enumerate(reader, start=2):
            for col in ("ticker", "date", "event", "detail", "status", "source"):
                if not row[col].strip():
                    raise EngineError(f"corporate_events.csv line {i}: {col} is required")
            date.fromisoformat(row["date"])
            rows.append({c: row[c].strip() for c in _EVENT_COLUMNS})
    return sorted(rows, key=lambda r: (r["date"], r["ticker"], r["event"]))


def verify_events(
    events: list[dict[str, str]], records: dict[str, list[EodRecord]]
) -> list[dict]:
    """Check each completed split / spin-off is already adjusted in the frame.

    On the first trading day on/after the event date, the ticker's close move
    must be ordinary (|move| <= UNADJUSTED_MOVE); a gap means the vendor left
    the action in the raw series and the index would book a fake loss/gain.
    """
    out = []
    for ev in events:
        row: dict = dict(ev)
        recs = records.get(ev["ticker"], [])
        if ev["event"] in ADJUSTED_KINDS and ev["status"] == "completed" and recs:
            when = date.fromisoformat(ev["date"])
            idx = next((i for i, r in enumerate(recs) if r.date >= when), None)
            if idx is not None and idx > 0:
                move = recs[idx].close / recs[idx - 1].close - 1
                row["frame_date"] = recs[idx].date.isoformat()
                row["frame_move"] = _r(move)
                if abs(move) > UNADJUSTED_MOVE:
                    raise EngineError(
                        f"{ev['ticker']} {ev['event']} on {ev['date']}: close moved "
                        f"{move:+.1%} — the frame looks unadjusted for this action"
                    )
                row["verified_adjusted"] = True
        out.append(row)
    return out


def load_categories() -> dict[str, dict[str, str]]:
    """{index_id: {ticker: category}} from the validated universe files."""
    sys.path.insert(0, str(_ROOT))
    from data.large_cap import load_large_cap
    from data.mixed_cap import load_mixed_cap

    large = {e.ticker: e.category for e in load_large_cap()}
    mixed = load_mixed_cap()
    return {
        "AS-100": large,
        "AS-MCG100": {e.ticker: e.category for e in mixed if e.in_mcg},
        "AS-MCB100": {e.ticker: e.category for e in mixed if e.in_mcb},
    }


# -- computation -------------------------------------------------------------


def compute_research_index(
    index_id: str,
    composition: list[str],
    records: dict[str, list[EodRecord]],
    exclusions: list[Exclusion] | None = None,
    start: date = FRAME_START,
    exclusion_universe: str | None = None,
    listing_dates: dict[str, date] | None = None,
) -> ResearchIndex:
    """Run one equal-weight index over the frame with point-in-time entry.

    A name is eligible on a day only when it has a close that day *and* the
    day is on/after its documented regular-way listing date (if any).
    """
    listed = listing_dates or {}
    universe = exclusion_universe or UNIVERSE_LABELS.get(index_id, index_id)
    missing = {t for t in composition if not records.get(t)}
    effective, surprises = reconcile(
        exclusions or [], universe, set(composition) - missing, missing
    )
    if surprises:
        raise EngineError(
            f"{index_id}: exclusion surprises need reconciliation: "
            f"{sorted(s.ticker for s in surprises)}"
        )
    excluded = {e.ticker for e in effective}
    candidates = sorted(t for t in composition if t not in excluded)
    if not candidates:
        raise EngineError(f"{index_id}: no constituents with data")

    lookup: dict[tuple[str, date], EodRecord] = {}
    days: set[date] = set()
    for t in candidates:
        for r in records[t]:
            if r.date >= start:
                lookup[(t, r.date)] = r
                days.add(r.date)
    trading_days = sorted(days)
    if not trading_days:
        raise EngineError(f"{index_id}: no trading days on/after {start}")
    base = trading_days[0]

    def eligible(t: str, day: date) -> bool:
        return (t, day) in lookup and day >= listed.get(t, date.min)

    initial = [t for t in candidates if eligible(t, base)]
    deferred = [t for t in candidates if t not in initial]
    if not initial:
        raise EngineError(f"{index_id}: no constituent trades on base date {base}")
    pre_listing: list[IndexEvent] = []
    for t in deferred:
        first = records[t][0].date
        if t not in listed:
            logger.warning("%s: %s first trades %s with no documented listing date — "
                           "add it to data/research/listing_dates.csv", index_id, t, first)
        elif first < listed[t]:
            pre_listing.append(IndexEvent(
                listed[t], "pre_listing_prints_ignored",
                f"{t}: vendor prints from {first} precede regular-way listing {listed[t]}",
            ))

    # Rebalancing on the final observed day is a no-op for levels and the
    # month may be incomplete — skip it so a mid-month frame end never
    # masquerades as a quarter end.
    quarter_ends = rebalance_dates(trading_days) - {trading_days[-1]}

    engine = EqualWeightEngine(composition=list(initial))
    engine.events.extend(pre_listing)
    engine.initialize(base, {t: lookup[(t, base)] for t in initial})
    last: dict[str, EodRecord] = {t: lookup[(t, base)] for t in initial}

    levels = {base: BASE_LEVEL}
    counts = {base: len(initial)}
    current = list(initial)
    entries: list[tuple[date, str]] = []
    for day in trading_days[1:]:
        prices: dict[str, EodRecord] = {}
        for t in current:
            rec = lookup.get((t, day))
            if rec is None:
                rec = last[t]
                engine.events.append(
                    IndexEvent(day, "stale_close", f"{t}: no close; carried {rec.date}")
                )
            prices[t] = rec
            last[t] = rec
        level = engine.step(day, prices)
        if day in quarter_ends:
            joining = [t for t in deferred if eligible(t, day)]
            if joining:
                for t in joining:
                    prices[t] = lookup[(t, day)]
                    last[t] = prices[t]
                    entries.append((day, t))
                    engine.events.append(
                        IndexEvent(day, "entry", f"{t}: first quarterly rebalance after listing")
                    )
                deferred = [t for t in deferred if t not in joining]
                current = sorted(current + joining)
            engine.rebalance(day, prices, current)
        levels[day] = level
        counts[day] = len(current)

    for t in deferred:
        logger.warning("%s: %s never reached a rebalance inside the frame", index_id, t)

    return ResearchIndex(
        index_id=index_id, base_date=base, levels=levels, counts=counts,
        constituents=current, initial=initial, entries=entries,
        excluded=effective, events=engine.events,
    )


def series_stats(levels: dict[date, float]) -> dict:
    """Total return, CAGR, annualized vol, max drawdown, calendar-year returns."""
    days = sorted(levels)
    if len(days) < 2:
        raise EngineError("need at least two observations for statistics")
    first, last = levels[days[0]], levels[days[-1]]
    years = (days[-1] - days[0]).days / 365.25
    rets = [levels[b] / levels[a] - 1 for a, b in zip(days, days[1:], strict=False)]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    vol = math.sqrt(var) * math.sqrt(TRADING_DAYS_PER_YEAR)

    peak, peak_day = first, days[0]
    mdd, mdd_peak, mdd_trough = 0.0, days[0], days[0]
    for d in days:
        if levels[d] > peak:
            peak, peak_day = levels[d], d
        dd = levels[d] / peak - 1
        if dd < mdd:
            mdd, mdd_peak, mdd_trough = dd, peak_day, d

    calendar: dict[str, float] = {}
    prev = first
    for year in sorted({d.year for d in days}):
        year_days = [d for d in days if d.year == year]
        calendar[str(year)] = levels[year_days[-1]] / prev - 1
        prev = levels[year_days[-1]]

    cagr = (last / first) ** (1 / years) - 1 if years > 0 else 0.0
    return {
        "start": days[0].isoformat(),
        "end": days[-1].isoformat(),
        "total_return": last / first - 1,
        "cagr": cagr,
        "ann_vol": vol,
        "return_to_vol": cagr / vol if vol > 0 else 0.0,
        "max_drawdown": mdd,
        "max_drawdown_peak": mdd_peak.isoformat(),
        "max_drawdown_trough": mdd_trough.isoformat(),
        "calendar_returns": calendar,
    }


def category_breakdown(
    index_id: str,
    composition: list[str],
    categories: dict[str, str],
    records: dict[str, list[EodRecord]],
    excluded: set[str],
    listing_dates: dict[str, date] | None = None,
) -> list[dict]:
    """Equal-weight sub-index per category, same point-in-time rules."""
    by_cat: dict[str, list[str]] = {}
    for t in composition:
        if t in excluded:
            continue
        by_cat.setdefault(categories.get(t, "Uncategorized"), []).append(t)
    out = []
    for cat, tickers in sorted(by_cat.items()):
        sub = compute_research_index(f"{index_id}:{cat}", tickers, records, [], FRAME_START,
                                     exclusion_universe="-", listing_dates=listing_dates)
        stats = series_stats(sub.levels)
        out.append({
            "category": cat,
            "constituents": len(tickers),
            "total_return": stats["total_return"],
            "cagr": stats["cagr"],
            "ann_vol": stats["ann_vol"],
            "max_drawdown": stats["max_drawdown"],
        })
    out.sort(key=lambda r: -r["total_return"])
    return out


# -- serialization -----------------------------------------------------------


def _r(x: float, nd: int = 6) -> float:
    return float(f"{x:.{nd}f}")


def _round_stats(stats: dict) -> dict:
    out = {}
    for k, v in stats.items():
        if isinstance(v, float):
            out[k] = _r(v)
        elif isinstance(v, dict):
            out[k] = {kk: _r(vv) for kk, vv in v.items()}
        else:
            out[k] = v
    return out


def levels_csv(indices: list[ResearchIndex]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["index_id", "date", "level", "constituents"])
    for idx in indices:
        for d in sorted(idx.levels):
            w.writerow([idx.index_id, d.isoformat(), f"{idx.levels[d]:.6f}", idx.counts[d]])
    return buf.getvalue()


def build_payload(
    indices: list[ResearchIndex],
    records: dict[str, list[EodRecord]],
    categories: dict[str, dict[str, str]],
    compositions: dict[str, list[str]],
    frame_sha256: str,
    source: str,
    listing_dates: dict[str, date] | None = None,
    corporate_events: list[dict] | None = None,
) -> dict:
    """Everything the metadata file and the Explorer need, deterministic."""
    events = corporate_events or []
    base = indices[0].base_date
    proxies = sorted(set(PROXY_MAP))
    bench = {p: rebase_proxy(p, records.get(p, []), base) for p in proxies}
    end = max(max(i.levels) for i in indices)
    # AS-100 must stay disjoint from both mixed-cap indices; MCG/MCB may share.
    overlap = sorted(
        (set(compositions["AS-MCG100"]) | set(compositions["AS-MCB100"]))
        & set(compositions["AS-100"])
    )
    shared_mixed = sorted(set(compositions["AS-MCG100"]) & set(compositions["AS-MCB100"]))

    payload_indices = {}
    for idx in indices:
        excluded = {e.ticker for e in idx.excluded}
        shown = list(INDEX_BENCHMARKS[idx.index_id]) + list(THEMATIC_PROXIES)
        payload_indices[idx.index_id] = {
            "index_id": idx.index_id,
            "base_date": idx.base_date.isoformat(),
            "constituents_at_base": len(idx.initial),
            "constituents_at_end": len(idx.constituents),
            "entries": [{"date": d.isoformat(), "ticker": t} for d, t in idx.entries],
            "excluded": [{"ticker": e.ticker, "reason": e.reason} for e in idx.excluded],
            "events": [
                {"date": e.date.isoformat(), "kind": e.kind, "detail": e.detail}
                for e in idx.events
            ],
            "stats": _round_stats(series_stats(idx.levels)),
            "categories": [
                {k: (_r(v) if isinstance(v, float) else v) for k, v in row.items()}
                for row in category_breakdown(
                    idx.index_id, compositions[idx.index_id],
                    categories[idx.index_id], records, excluded, listing_dates,
                )
            ],
            "corporate_events": [
                e for e in events if e["ticker"] in set(compositions[idx.index_id])
            ],
            "broad_benchmarks": list(INDEX_BENCHMARKS[idx.index_id]),
            "thematic_benchmarks": list(THEMATIC_PROXIES),
            "levels": {d.isoformat(): _r(idx.levels[d], 2) for d in sorted(idx.levels)},
            "benchmarks": {
                p: {
                    "name": PROXY_MAP[p],
                    "stats": _round_stats(series_stats(
                        {d: v for d, v in bench[p].levels.items() if d <= end}
                    )),
                    "levels": {
                        d.isoformat(): _r(v, 2)
                        for d, v in sorted(bench[p].levels.items()) if d <= end
                    },
                }
                for p in shown
            },
        }

    return {
        "kind": "research-backfill",
        "methodology_version": METHODOLOGY_VERSION,
        "frame": {"start": base.isoformat(), "end": end.isoformat()},
        "source": source,
        "frame_sha256": frame_sha256,
        "caveats": list(CAVEATS),
        "caveats_zh": list(CAVEATS_ZH),
        "universe_overlap": overlap,
        "mixed_cap_shared": len(shared_mixed),
        "listing_dates": {t: d.isoformat() for t, d in sorted((listing_dates or {}).items())},
        "benchmark_proxy_mapping": dict(sorted(PROXY_MAP.items())),
        "indices": payload_indices,
    }


def write_outputs(out_dir: Path, indices: list[ResearchIndex], payload: dict) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    levels = levels_csv(indices)
    meta = dict(payload)
    meta["sha256_research_levels_csv"] = hashlib.sha256(levels.encode()).hexdigest()
    files = {
        "research_levels.csv": levels,
        "research_metadata.json": json.dumps(meta, indent=2, sort_keys=True,
                                             ensure_ascii=False) + "\n",
    }
    hashes = {}
    for name, text in files.items():
        (out_dir / name).write_text(text, encoding="utf-8")
        hashes[name] = hashlib.sha256(text.encode()).hexdigest()
    return hashes


def web_fixture(payload: dict) -> str:
    """Explorer payload: drop per-day event noise, keep everything displayed."""
    slim = json.loads(json.dumps(payload))
    for idx in slim["indices"].values():
        idx["event_counts"] = {}
        for ev in idx.pop("events"):
            idx["event_counts"][ev["kind"]] = idx["event_counts"].get(ev["kind"], 0) + 1
    return json.dumps(slim, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


# -- CLI ---------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pipelines.indices.research",
        description="Backfill AS-100 / AS-MCG100 / AS-MCB100 over the 2024-01-02 research frame.",
    )
    parser.add_argument("--frame", default=str(DEFAULT_FRAME))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--web-out", default=None, help="write the Explorer research fixture")
    parser.add_argument("--refresh", action="store_true",
                        help="re-pull the frame from Yahoo first (network)")
    parser.add_argument("--end", default=None, help="frame end date for --refresh (ISO)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    from .yahoo import SOURCE_LABEL, fetch_frame, frame_csv, read_frame_text, write_frame

    compositions = load_compositions()
    frame_path = Path(args.frame)
    if args.refresh:
        if not args.end:
            parser.error("--refresh needs --end (explicit frame end keeps runs reproducible)")
        universe_of = {}
        for idx_id, label in UNIVERSE_LABELS.items():
            for t in compositions[idx_id]:
                universe_of[t] = f"{universe_of[t]};{label}" if t in universe_of else label
        for p in PROXY_MAP:
            universe_of.setdefault(p, "Benchmark")
        rows = fetch_frame(sorted(universe_of), FRAME_START, date.fromisoformat(args.end))
        write_frame(frame_path, frame_csv(rows, universe_of))
        logger.info("wrote research frame %s", frame_path)
    if not frame_path.is_file():
        parser.error(f"{frame_path} not found — run with --refresh --end YYYY-MM-DD first")

    text = read_frame_text(frame_path)
    records = parse_frame(text)
    exclusions = load_exclusions()
    listing_dates = load_listing_dates()
    corporate_events = verify_events(load_corporate_events(), records)
    indices = [
        compute_research_index(i, compositions[i], records, exclusions,
                               listing_dates=listing_dates)
        for i in INDEX_IDS
    ]
    payload = build_payload(
        indices, records, load_categories(), compositions,
        hashlib.sha256(text.encode()).hexdigest(), SOURCE_LABEL, listing_dates,
        corporate_events,
    )
    for name, digest in sorted(write_outputs(Path(args.out_dir), indices, payload).items()):
        print(f"{name}: {digest}")
    if args.web_out:
        out = Path(args.web_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(web_fixture(payload), encoding="utf-8")
        print(f"wrote {out}")
    for idx_id, idx in payload["indices"].items():
        s = idx["stats"]
        print(f"{idx_id}: {s['start']}..{s['end']} total {s['total_return']:+.1%} "
              f"CAGR {s['cagr']:+.1%} vol {s['ann_vol']:.1%} maxDD {s['max_drawdown']:.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
