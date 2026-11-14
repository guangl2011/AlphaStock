"""Build the AS-MCG100 / AS-MCB100 mixed-cap universes (methodology v4.0).

Inputs, all checked in so the build is reproducible offline:

* ``data/research/universe_screen.json`` — Yahoo screen snapshot (market cap,
  price, 3-month average volume, quarterly YoY revenue growth) for every
  candidate surfaced by You.com discovery, the prior Mixed universe, and the
  2026-11-09 rebalance names.
* ``data/research/theme_map.csv`` — the human call: which candidates belong to
  the AI-energy / Physical-AI theme, their category (11-category taxonomy),
  and their growth leg (G1-G4) with a one-line exposure note.

Rules (design doc §3, v4.0 bands):

* Cap bands: Mid $2B-<$10B, Small $250M-<$2B, Micro <$250M (floor $25M).
* Tradability: price >= $0.75; 3-month average daily dollar volume
  Mid >= $10M, Small >= $3M, Micro >= $1M.
* Growth: G1 must be evidenced (YoY revenue growth >= 10%); a G1 claim the
  data contradicts disqualifies the name. G2-G4 are qualitative, flagged for
  re-verification at the monthly review.
* Ranking within a band: G1 names first, then qualitative legs; ties broken
  by dollar volume, then ticker. AS-MCG100 takes 40/40/20 and AS-MCB100 takes
  70/25/5 from the same ranked bands, so the two may share names. Names
  >= $10B belong to AS-100 and are rejected here.

Output: ``data/mixed_cap_universe.csv`` (deterministic, sorted by ticker).

    python -m pipelines.indices.universe_build
"""

from __future__ import annotations

import csv
import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
SCREEN_JSON = _ROOT / "data" / "research" / "universe_screen.json"
THEME_CSV = _ROOT / "data" / "research" / "theme_map.csv"
OUT_CSV = _ROOT / "data" / "mixed_cap_universe.csv"
SCREEN_AS_OF = "2026-11-13"

BANDS = ("Mid", "Small", "Micro")
BAND_RANGES = {"Mid": (2e9, 10e9), "Small": (250e6, 2e9), "Micro": (25e6, 250e6)}
MIN_ADT = {"Mid": 10e6, "Small": 3e6, "Micro": 1e6}
MIN_PRICE = 0.75
G1_THRESHOLD = 0.10
#: Constituent counts per band (equal weight, so counts are the weights).
TARGETS: dict[str, dict[str, int]] = {
    "AS-MCG100": {"Mid": 40, "Small": 40, "Micro": 20},
    "AS-MCB100": {"Mid": 70, "Small": 25, "Micro": 5},
}
CATEGORIES = frozenset({
    "AI & Compute", "Cooling & Power Management", "Data-Center REITs & Digital Infrastructure",
    "Engineering & Construction", "Grid Equipment & Transformers", "Natural Gas Midstream / LNG",
    "Nuclear Fuel & SMRs", "Other Adjacent", "Physical AI", "Power Producers & IPPs",
    "Utilities (Data-Center Load)",
})

COLUMNS = (
    "ticker", "company", "category", "cap_band", "market_cap_usd", "market_cap_as_of",
    "price", "adt_3m_usd", "revenue_growth_yoy", "growth_leg", "growth_note",
    "in_mcg", "in_mcb", "rank_in_band", "source_url",
)


class UniverseBuildError(ValueError):
    """Raised when inputs cannot produce the contracted universes."""


@dataclass(frozen=True)
class Candidate:
    ticker: str
    company: str
    category: str
    band: str
    market_cap: float
    price: float
    adt: float
    growth: float | None
    leg: str
    note: str

    @property
    def sort_key(self) -> tuple:
        return (0 if self.leg == "G1" else 1, -self.adt, self.ticker)


def band_of(market_cap: float) -> str | None:
    for band, (lo, hi) in BAND_RANGES.items():
        if lo <= market_cap < hi:
            return band
    return None


def screen(theme: dict[str, dict[str, str]], quotes: dict[str, dict]) -> tuple[
    list[Candidate], list[dict[str, str]]
]:
    """Apply band, tradability and growth rules; return passes and the reject log."""
    passed: list[Candidate] = []
    rejects: list[dict[str, str]] = []
    for ticker in sorted(theme):
        row = theme[ticker]
        if row["category"] not in CATEGORIES:
            raise UniverseBuildError(f"{ticker}: unknown category {row['category']!r}")
        q = quotes.get(ticker) or {}
        mc, px = q.get("marketCap"), q.get("currentPrice") or q.get("regularMarketPrice")
        vol = q.get("averageDailyVolume3Month") or q.get("averageVolume")
        if not mc or not px or not vol:
            rejects.append({"ticker": ticker, "reason": "no screen data"})
            continue
        band = band_of(float(mc))
        adt = float(vol) * float(px)
        growth = q.get("revenueGrowth")
        reason = ""
        if band is None:
            reason = "outside mixed-cap bands" + (" (>= $10B: AS-100 territory)" if mc >= 10e9
                                                  else " (< $25M floor)")
        elif px < MIN_PRICE:
            reason = f"price {px:.2f} < {MIN_PRICE}"
        elif adt < MIN_ADT[band]:
            reason = f"ADT ${adt / 1e6:.1f}M < ${MIN_ADT[band] / 1e6:.0f}M ({band})"
        elif row["growth_leg"] == "G1" and (growth is None or growth < G1_THRESHOLD):
            reason = "G1 claimed but YoY revenue growth " + (
                "unavailable" if growth is None else f"{growth:.0%}") + " < 10%"
        if reason:
            rejects.append({"ticker": ticker, "reason": reason})
            continue
        passed.append(Candidate(
            ticker, str(q.get("longName") or ticker), row["category"], band,
            float(mc), float(px), adt, None if growth is None else float(growth),
            row["growth_leg"], row["note"],
        ))
    return passed, rejects


def select(passed: list[Candidate]) -> dict[str, dict[str, list[Candidate]]]:
    ranked = {b: sorted((c for c in passed if c.band == b), key=lambda c: c.sort_key)
              for b in BANDS}
    out: dict[str, dict[str, list[Candidate]]] = {}
    for index_id, targets in TARGETS.items():
        out[index_id] = {}
        for band, n in targets.items():
            if len(ranked[band]) < n:
                raise UniverseBuildError(
                    f"{index_id}: {band} band has {len(ranked[band])} qualifying names, needs {n}"
                )
            out[index_id][band] = ranked[band][:n]
    return out


def universe_csv(passed: list[Candidate], chosen: dict[str, dict[str, list[Candidate]]]) -> str:
    mcg = {c.ticker for cs in chosen["AS-MCG100"].values() for c in cs}
    mcb = {c.ticker for cs in chosen["AS-MCB100"].values() for c in cs}
    rank = {}
    for band in BANDS:
        for i, c in enumerate(sorted((c for c in passed if c.band == band),
                                     key=lambda c: c.sort_key), start=1):
            rank[c.ticker] = i
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COLUMNS)
    for c in sorted((c for c in passed if c.ticker in mcg | mcb), key=lambda c: c.ticker):
        w.writerow([
            c.ticker, c.company, c.category, c.band, f"{c.market_cap:.0f}", SCREEN_AS_OF,
            f"{c.price:.2f}", f"{c.adt:.0f}",
            "" if c.growth is None else f"{c.growth:.4f}", c.leg, c.note,
            "1" if c.ticker in mcg else "0", "1" if c.ticker in mcb else "0",
            rank[c.ticker], f"https://finance.yahoo.com/quote/{c.ticker}",
        ])
    return buf.getvalue()


def load_theme(path: Path = THEME_CSV) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return {r["ticker"].strip().upper(): r for r in csv.DictReader(f)}


def main(argv: list[str] | None = None) -> int:
    quotes = json.loads(SCREEN_JSON.read_text(encoding="utf-8"))
    passed, rejects = screen(load_theme(), quotes)
    chosen = select(passed)
    OUT_CSV.write_text(universe_csv(passed, chosen), encoding="utf-8")
    for index_id, bands in chosen.items():
        print(index_id, {b: len(cs) for b, cs in bands.items()})
    for r in rejects:
        print(f"  rejected {r['ticker']}: {r['reason']}")
    print(f"wrote {OUT_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
