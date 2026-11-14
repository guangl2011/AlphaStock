"""Contract tests for the AS-MCG100 / AS-MCB100 universe file (methodology v4.0)."""

from __future__ import annotations

import csv
import io
from collections import Counter
from pathlib import Path

import pytest

from data.large_cap import load_large_cap
from data.mixed_cap import (
    MIXED_CAP_CSV,
    MixedCapValidationError,
    compositions,
    load_mixed_cap,
)


def test_checked_in_universe_meets_band_targets() -> None:
    entries = load_mixed_cap()
    mcg = Counter(e.cap_band for e in entries if e.in_mcg)
    mcb = Counter(e.cap_band for e in entries if e.in_mcb)
    assert dict(mcg) == {"Mid": 40, "Small": 40, "Micro": 20}
    assert dict(mcb) == {"Mid": 70, "Small": 25, "Micro": 5}


def test_disjoint_from_as100_but_mcg_mcb_share_names() -> None:
    comp = compositions()
    large = {e.ticker for e in load_large_cap()}
    assert not large & set(comp["AS-MCG100"])
    assert not large & set(comp["AS-MCB100"])
    assert len(set(comp["AS-MCG100"]) & set(comp["AS-MCB100"])) == 70


def _mutate(tmp_path: Path, fn) -> Path:
    rows = list(csv.DictReader(MIXED_CAP_CSV.open(encoding="utf-8")))
    fn(rows)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0]), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    path = tmp_path / "u.csv"
    path.write_text(buf.getvalue(), encoding="utf-8")
    return path


def test_band_must_match_market_cap(tmp_path: Path) -> None:
    def bad(rows):
        row = next(r for r in rows if r["cap_band"] == "Micro")
        row["market_cap_usd"] = "5000000000"
    with pytest.raises(MixedCapValidationError, match="outside Micro band"):
        load_mixed_cap(_mutate(tmp_path, bad))


def test_band_counts_are_enforced(tmp_path: Path) -> None:
    def bad(rows):
        next(r for r in rows if r["in_mcg"] == "1" and r["in_mcb"] == "1")["in_mcg"] = "0"
    with pytest.raises(MixedCapValidationError, match="band counts"):
        load_mixed_cap(_mutate(tmp_path, bad))
