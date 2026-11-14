"""Universe builder: rules, determinism, and parity with the committed CSV."""

from __future__ import annotations

import json

from pipelines.indices import universe_build as ub


def _run():
    quotes = json.loads(ub.SCREEN_JSON.read_text(encoding="utf-8"))
    passed, rejects = ub.screen(ub.load_theme(), quotes)
    return passed, rejects, ub.select(passed)


def test_build_reproduces_committed_universe() -> None:
    passed, _, chosen = _run()
    assert ub.universe_csv(passed, chosen) == ub.OUT_CSV.read_text(encoding="utf-8")


def test_rules_band_tradability_and_g1_evidence() -> None:
    passed, rejects, _ = _run()
    for c in passed:
        lo, hi = ub.BAND_RANGES[c.band]
        assert lo <= c.market_cap < hi
        assert c.price >= ub.MIN_PRICE and c.adt >= ub.MIN_ADT[c.band]
        if c.leg == "G1":
            assert c.growth is not None and c.growth >= ub.G1_THRESHOLD
    assert all(r["reason"] for r in rejects)


def test_g1_ranks_before_qualitative_legs() -> None:
    _, _, chosen = _run()
    for bands in chosen.values():
        for names in bands.values():
            legs = [c.leg == "G1" for c in names]
            assert legs == sorted(legs, reverse=True)


def test_band_of_boundaries() -> None:
    assert ub.band_of(10e9) is None
    assert ub.band_of(9.99e9) == "Mid"
    assert ub.band_of(2e9) == "Mid"
    assert ub.band_of(250e6) == "Small"
    assert ub.band_of(249e6) == "Micro"
    assert ub.band_of(24e6) is None
