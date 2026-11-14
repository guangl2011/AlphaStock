"""Loader for the AS-MCG100 / AS-MCB100 mixed-cap universes (methodology v4.0).

``mixed_cap_universe.csv`` is data, not code: it is produced by
``python -m pipelines.indices.universe_build`` from the checked-in research
inputs and then PR-reviewed. Either a human, Claude Code, or an Obvious agent
may edit it at a monthly review — the loader is what keeps every edit honest:

* every row's ``cap_band`` matches its ``market_cap_usd``
  (Mid $2B-<$10B, Small $250M-<$2B, Micro $25M-<$250M);
* AS-MCG100 holds exactly 40 Mid / 40 Small / 20 Micro and AS-MCB100 exactly
  70 Mid / 25 Small / 5 Micro (equal weight, so counts are the weights);
* the two mixed-cap indices may share names, but neither may hold an AS-100
  (Large-Cap, >= $10B) constituent.
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).parent
MIXED_CAP_CSV = DATA_DIR / "mixed_cap_universe.csv"

REQUIRED_COLUMNS = (
    "ticker", "company", "category", "cap_band", "market_cap_usd", "market_cap_as_of",
    "price", "adt_3m_usd", "revenue_growth_yoy", "growth_leg", "growth_note",
    "in_mcg", "in_mcb", "rank_in_band", "source_url",
)
BAND_RANGES = {"Mid": (2e9, 10e9), "Small": (250e6, 2e9), "Micro": (25e6, 250e6)}
TARGETS = {
    "AS-MCG100": {"Mid": 40, "Small": 40, "Micro": 20},
    "AS-MCB100": {"Mid": 70, "Small": 25, "Micro": 5},
}
GROWTH_LEGS = frozenset({"G1", "G2", "G3", "G4"})


class MixedCapValidationError(ValueError):
    """Raised when mixed_cap_universe.csv does not meet the v4.0 contract."""


@dataclass(frozen=True)
class MixedCapEntry:
    ticker: str
    company: str
    category: str
    cap_band: str
    market_cap_usd: float
    market_cap_as_of: str
    growth_leg: str
    growth_note: str
    in_mcg: bool
    in_mcb: bool
    source_url: str


def load_mixed_cap(path: Path | str = MIXED_CAP_CSV) -> list[MixedCapEntry]:
    path = Path(path)
    if not path.is_file():
        raise MixedCapValidationError(f"mixed-cap universe file not found: {path}")
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise MixedCapValidationError(f"mixed_cap_universe.csv missing column(s): {missing}")
        rows = [dict(r) for r in reader]
    out: list[MixedCapEntry] = []
    seen: set[str] = set()
    for i, row in enumerate(rows, start=2):
        t = row["ticker"].strip().upper()
        if not t or t in seen:
            raise MixedCapValidationError(f"line {i}: missing or duplicate ticker {t!r}")
        seen.add(t)
        band = row["cap_band"].strip()
        if band not in BAND_RANGES:
            raise MixedCapValidationError(f"{t}: unknown cap_band {band!r}")
        cap = float(row["market_cap_usd"])
        lo, hi = BAND_RANGES[band]
        if not lo <= cap < hi:
            raise MixedCapValidationError(f"{t}: market cap {cap:,.0f} is outside {band} band")
        if row["growth_leg"].strip() not in GROWTH_LEGS:
            raise MixedCapValidationError(f"{t}: growth_leg must be one of {sorted(GROWTH_LEGS)}")
        for col in ("company", "category", "growth_note", "source_url", "market_cap_as_of"):
            if not row[col].strip():
                raise MixedCapValidationError(f"{t}: empty required field {col!r}")
        mcg, mcb = row["in_mcg"].strip() == "1", row["in_mcb"].strip() == "1"
        if not (mcg or mcb):
            raise MixedCapValidationError(f"{t}: row belongs to neither index")
        out.append(MixedCapEntry(
            t, row["company"].strip(), row["category"].strip(), band, cap,
            row["market_cap_as_of"].strip(), row["growth_leg"].strip(),
            row["growth_note"].strip(), mcg, mcb, row["source_url"].strip(),
        ))
    for index_id, targets in TARGETS.items():
        flag = "in_mcg" if index_id == "AS-MCG100" else "in_mcb"
        got = Counter(e.cap_band for e in out if getattr(e, flag))
        if dict(got) != targets:
            raise MixedCapValidationError(f"{index_id} band counts {dict(got)} != {targets}")
    return out


def compositions(entries: list[MixedCapEntry] | None = None) -> dict[str, list[str]]:
    entries = entries if entries is not None else load_mixed_cap()
    return {
        "AS-MCG100": sorted(e.ticker for e in entries if e.in_mcg),
        "AS-MCB100": sorted(e.ticker for e in entries if e.in_mcb),
    }
