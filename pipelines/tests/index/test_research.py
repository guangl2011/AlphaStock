"""Research-frame backfill (2024-01-02 forward): engine rules, stats, artifacts."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from pipelines.indices import research
from pipelines.indices.engine import EngineError, EodRecord
from pipelines.indices.exclusions import Exclusion
from pipelines.indices.yahoo import frame_csv, read_frame_text, write_frame

WEB_FIXTURE = Path(__file__).resolve().parents[3] / "web" / "fixtures" / "research.json"


def _weekdays(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _series(days: list[date], start_px: float, daily: float) -> list[EodRecord]:
    out, px = [], start_px
    for d in days:
        out.append(EodRecord(d, px, px))
        px *= 1 + daily
    return out


DAYS = _weekdays(date(2024, 1, 2), date(2024, 7, 31))


def test_base_is_first_trading_day_at_1000():
    recs = {"A": _series(DAYS, 10, 0.001), "B": _series(DAYS, 50, -0.001)}
    idx = research.compute_research_index("AS-100", ["A", "B"], recs)
    assert idx.base_date == date(2024, 1, 2)
    assert idx.levels[idx.base_date] == 1000.0
    assert idx.initial == ["A", "B"]


def test_equal_weight_two_names_one_day():
    recs = {
        "A": [EodRecord(DAYS[0], 10, 10), EodRecord(DAYS[1], 11, 11)],
        "B": [EodRecord(DAYS[0], 20, 20), EodRecord(DAYS[1], 20, 20)],
    }
    idx = research.compute_research_index("AS-100", ["A", "B"], recs)
    assert idx.levels[DAYS[1]] == pytest.approx(1050.0)  # (+10% + 0%) / 2


def test_late_lister_enters_at_next_quarter_end_not_before():
    late_days = [d for d in DAYS if d >= date(2024, 2, 15)]
    recs = {
        "A": _series(DAYS, 10, 0.0),
        "B": _series(DAYS, 10, 0.0),
        "NEW": _series(late_days, 5, 0.05),  # rockets before entry — must not count
    }
    idx = research.compute_research_index("AS-100", ["A", "B", "NEW"], recs)
    q1_end = date(2024, 3, 29)
    assert idx.entries == [(q1_end, "NEW")]
    assert idx.levels[date(2024, 3, 28)] == pytest.approx(1000.0)
    assert idx.counts[date(2024, 3, 28)] == 2 and idx.counts[q1_end] == 3


def test_listing_date_gates_pre_listing_prints():
    recs = {
        "A": _series(DAYS, 10, 0.0),
        "WI": _series([d for d in DAYS if d >= date(2024, 3, 27)], 100, 0.0),
    }
    listed = {"WI": date(2024, 4, 2)}
    gated = research.compute_research_index("AS-100", ["A", "WI"], recs, listing_dates=listed)
    assert gated.entries == [(date(2024, 6, 28), "WI")]
    assert any(e.kind == "pre_listing_prints_ignored" for e in gated.events)
    ungated = research.compute_research_index("AS-100", ["A", "WI"], recs)
    assert ungated.entries == [(date(2024, 3, 29), "WI")]


def test_listing_dates_file_requires_source(tmp_path):
    p = tmp_path / "l.csv"
    p.write_text('"ticker","regular_way_first_trade","event","source"\n"X","2024-01-05","IPO",""\n')
    with pytest.raises(EngineError, match="source is required"):
        research.load_listing_dates(p)


def test_checked_in_listing_dates_cover_known_late_listers():
    listed = research.load_listing_dates()
    assert listed["GEV"] == date(2024, 4, 2)
    assert {"ALAB", "CORZ", "CRWV", "NBIS", "NNE"} <= set(listed)


def test_stale_close_carries_forward_loudly():
    gap = DAYS[5]
    recs = {
        "A": [r for r in _series(DAYS, 10, 0.01) if r.date != gap],
        "B": _series(DAYS, 10, 0.0),
    }
    idx = research.compute_research_index("AS-100", ["A", "B"], recs)
    assert any(e.kind == "stale_close" and e.date == gap for e in idx.events)
    assert gap in idx.levels


def test_documented_exclusion_is_excluded_and_undocumented_gap_fails():
    recs = {"A": _series(DAYS, 10, 0.0), "B": _series(DAYS, 10, 0.0)}
    doc = [Exclusion("GONE", "LargeCap100", "no history", "2026-11-10", "test")]
    idx = research.compute_research_index("AS-MC100", ["A", "B", "GONE"], recs, doc)
    assert [e.ticker for e in idx.excluded] == ["GONE"]
    with pytest.raises(EngineError, match="exclusion surprises"):
        research.compute_research_index("AS-MC100", ["A", "B", "GONE"], recs, [])


def test_series_stats_known_values():
    levels = {
        date(2024, 1, 2): 1000.0,
        date(2024, 6, 3): 1200.0,
        date(2024, 12, 31): 900.0,
        date(2025, 12, 31): 1350.0,
    }
    s = research.series_stats(levels)
    assert s["total_return"] == pytest.approx(0.35)
    assert s["max_drawdown"] == pytest.approx(-0.25)
    assert s["max_drawdown_peak"] == "2024-06-03"
    assert s["max_drawdown_trough"] == "2024-12-31"
    assert s["calendar_returns"] == {"2024": pytest.approx(-0.1), "2025": pytest.approx(0.5)}


def test_frame_roundtrip_is_byte_deterministic(tmp_path):
    rows = {"B": [(date(2024, 1, 3), 2.0, 1.9)], "A": [(date(2024, 1, 2), 1.0, 1.0)]}
    text = frame_csv(rows, {"A": "Mixed", "B": "Benchmark"})
    p1, p2 = tmp_path / "a.csv.gz", tmp_path / "b.csv.gz"
    write_frame(p1, text)
    write_frame(p2, text)
    assert p1.read_bytes() == p2.read_bytes()
    parsed = research.parse_frame(read_frame_text(p1))
    assert list(parsed) == ["A", "B"] and parsed["B"][0].adj_close == 1.9


@pytest.fixture(scope="module")
def full_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("research")
    web = out / "research.json"
    assert research.main(["--out-dir", str(out), "--web-out", str(web)]) == 0
    return out, web


def test_checked_in_frame_runs_and_spans_three_year_frame(full_run):
    out, _ = full_run
    meta = json.loads((out / "research_metadata.json").read_text())
    assert meta["frame"]["start"] == "2024-01-02"
    assert meta["frame"]["end"] >= "2026-11-01"
    assert set(meta["indices"]) == {"AS-100", "AS-MC100"}
    mc = meta["indices"]["AS-MC100"]
    assert mc["excluded"] == []
    assert mc["constituents_at_base"] == 97  # 100 minus ALAB, NBIS, CRWV (late listings)
    gev = [e for e in meta["indices"]["AS-100"]["entries"] if e["ticker"] == "GEV"]
    assert gev == [{"date": "2024-06-28", "ticker": "GEV"}]
    for idx in meta["indices"].values():
        assert idx["levels"]["2024-01-02"] == 1000.0
        assert set(idx["broad_benchmarks"]) <= set(idx["benchmarks"])
    assert meta["caveats"] and "survivorship" in meta["caveats"][0]


def test_committed_web_fixture_matches_regeneration(full_run):
    _, web = full_run
    assert WEB_FIXTURE.read_text() == web.read_text(), (
        "web/fixtures/research.json is stale — regenerate with "
        "`python -m pipelines.indices.research --web-out ../web/fixtures/research.json`"
    )


def test_research_run_is_deterministic(full_run, tmp_path):
    out, _ = full_run
    assert research.main(["--out-dir", str(tmp_path)]) == 0
    for name in ("research_levels.csv", "research_metadata.json"):
        assert (tmp_path / name).read_bytes() == (out / name).read_bytes()


# -- You.com corporate-event research ------------------------------------------


def test_checked_in_corporate_events_are_sourced_and_cover_vendor_adjustments():
    events = research.load_corporate_events()
    assert all(e["source"].startswith("https://") for e in events)
    by = {(e["ticker"], e["event"]) for e in events}
    # Every split/spin Yahoo folds into its close since 2024 has a sourced row.
    for t in ("NVDA", "AVGO", "SMCI", "LRCX", "ANET", "ETR", "POWL", "KLAC", "IESC"):
        assert (t, "split") in by
    for t in ("J", "WDC", "HON", "MOD"):
        assert (t, "spin_off") in by
    assert ("P", "ticker_change") in by and ("OKLO", "listing") in by


def test_corporate_events_require_source(tmp_path):
    p = tmp_path / "e.csv"
    p.write_text(
        '"ticker","date","event","detail","yahoo_factor","index_treatment","status","source"\n'
        '"X","2024-06-10","split","10-for-1","10","t","completed",""\n'
    )
    with pytest.raises(EngineError, match="source is required"):
        research.load_corporate_events(p)


def test_verify_events_flags_an_unadjusted_split():
    days = DAYS[:5]
    adjusted = {"X": _series(days, 100, 0.0)}
    raw = {"X": [EodRecord(d, 100 if i < 2 else 10, 100) for i, d in enumerate(days)]}
    ev = [{"ticker": "X", "date": days[2].isoformat(), "event": "split", "detail": "10-for-1",
           "yahoo_factor": "10", "index_treatment": "", "status": "completed", "source": "s"}]
    assert research.verify_events(ev, adjusted)[0]["verified_adjusted"] is True
    with pytest.raises(EngineError, match="unadjusted"):
        research.verify_events(ev, raw)


def test_oklo_spac_shell_history_is_gated(full_run):
    out, _ = full_run
    meta = json.loads((out / "research_metadata.json").read_text())
    entries = meta["indices"]["AS-100"]["entries"]
    assert {"date": "2024-06-28", "ticker": "OKLO"} in entries
    assert "OKLO" not in [e["ticker"] for e in entries if e["date"] < "2024-05-10"]


def test_ydc_extraction_finds_split_and_ignores_unanchored_text():
    from pipelines.indices.corp_events import extract

    payload = {"split": {"results": {"web": [
        {"url": "https://a", "title": "KLA Corporation sets 10-for-1 split", "page_age": "2026"},
        {"url": "https://b", "title": "Some other company 3-for-1 stock split"},
    ]}}}
    found = extract("KLAC", "KLA Corporation", payload)
    assert [(c.kind, c.url) for c in found] == [("split", "https://a")]
