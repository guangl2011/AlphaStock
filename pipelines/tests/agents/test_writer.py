"""Daily Alpha Writer acceptance tests (build spec verification table).

Fixture briefs in both languages, §7.2 structure and ordering, strength/
recency selection with a five-item cap, the explicit empty-run report,
Alpha Score annotations from the engine on main, display rules (no numeric
scenario ranges on the public issue), and the CLI's staging behavior. No
test touches the network.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pipelines.agents import (
    BANNED_LANGUAGE,
    MAX_BRIEF_ITEMS,
    verify_draft,
)
from pipelines.agents import writer as W
from pipelines.agents.daily_alpha import main
from pipelines.agents.writer import EmptyRunReport
from pipelines.scoring import alpha_score

AS_OF = datetime(2026, 11, 9, 11, 30, tzinfo=UTC)
URL = "https://www.pjm.com/"
INVALIDATION = "Invalidated if the project is withdrawn in a later scan."

def _row(
    signal_id: str,
    *,
    strength: int = 3,
    direction: str = "positive",
    tickers: tuple[str, ...] = ("GEV",),
    theme: str | None = "Grid & Transmission",
    category: str = "V2_INTERCONNECTION",
    age_hours: float = 1.0,
    summary_en: str = "PJM queue: a large load progressed to studied.",
    summary_zh: str = "PJM 并网队列：一个大负荷项目进入「研究」阶段。",
    macro: dict[str, str] | None = None,
    scenario_range: dict[str, float] | None = None,
) -> dict[str, object]:
    _ = scenario_range  # rows may carry it; the brief must never render it
    return {
        "signal_id": signal_id,
        "category": category,
        "event_type": "status_transition",
        "tickers": list(tickers),
        "theme": theme,
        "summary_en": summary_en,
        "summary_zh": summary_zh,
        "direction": direction,
        "strength": strength,
        "horizon_days": 90,
        "macro_overlay": macro or {"rate_sensitivity": "high", "usd_sensitivity": "low"},
        "invalidation": INVALIDATION,
        "sources": [{"type": "ISO_queue", "url": URL, "retrieved_at": "2026-11-09T01:00:00Z"}],
        "published_at": (AS_OF - timedelta(hours=age_hours)).isoformat(),
        "verified": False,
    }


class StubChecker:
    def __init__(self, *, fail_urls: frozenset[str] = frozenset()) -> None:
        self.fail_urls = fail_urls

    def reachable(self, url: str) -> bool:
        return url not in self.fail_urls


def verified_md(draft: W.DailyAlphaDraft, checker: StubChecker | None = None) -> str:
    """Verify the draft with a stub oracle and render its issue markdown."""
    return W.render_brief(
        draft,
        verify_draft(draft.verification_input(), source_checker=checker or StubChecker()),
    )


class TestBuildDailyAlpha:
    def test_brief_structure_both_languages(self) -> None:
        draft = W.build_daily_alpha(
            [
                _row("A", strength=3, tickers=("GEV",), theme="Grid & Transmission"),
                _row("B", strength=2, tickers=(), theme="Data Centers"),
            ],
            AS_OF,
        )
        assert isinstance(draft, W.DailyAlphaDraft)
        assert len(draft.items) == 2
        assert draft.title_en.startswith("Daily Alpha · ")
        assert draft.title_zh.startswith("每日信号 · ")
        assert draft.takeaway_en and draft.takeaway_zh
        assert draft.macro_en and draft.macro_zh
        # One item per theme: counts tie at 1, so names sort alphabetically.
        assert draft.theme_heat == (("Data Centers", 1), ("Grid & Transmission", 1))

    def test_theme_heat_counts_qualifying_batch(self) -> None:
        rows = [
            _row("A", theme="Grid & Transmission"),
            _row("B", theme="Grid & Transmission"),
            _row("C", theme="Data Centers"),
        ]
        draft = W.build_daily_alpha(rows, AS_OF)
        assert draft.theme_heat == (
            ("Grid & Transmission", 2),
            ("Data Centers", 1),
        )

    def test_takeaway_reflects_dominant_direction(self) -> None:
        rows = [
            _row("A", direction="negative"),
            _row("B", direction="negative"),
            _row("C"),
        ]
        draft = W.build_daily_alpha(rows, AS_OF)
        assert "cautious" in draft.takeaway_en
        assert "偏负面" in draft.takeaway_zh

    def test_max_five_items_strongest_first(self) -> None:
        rows = [_row(f"S{n:03d}", strength=2) for n in range(8)]
        rows.append(_row("TOP", strength=5))
        draft = W.build_daily_alpha(rows, AS_OF)
        assert draft is not None and not isinstance(draft, EmptyRunReport)
        assert len(draft.items) == MAX_BRIEF_ITEMS == 5
        assert draft.items[0].signal_id == "TOP"

    def test_ties_break_by_recency_then_id(self) -> None:
        draft = W.build_daily_alpha(
            [_row("OLD", age_hours=2.0), _row("NEW", age_hours=1.0)], AS_OF
        )
        assert isinstance(draft, W.DailyAlphaDraft)
        assert [r.signal_id for r in draft.items] == ["NEW", "OLD"]

    def test_strength_one_context_events_never_brief(self) -> None:
        report = W.build_daily_alpha([_row("A", strength=1)], AS_OF)
        assert isinstance(report, EmptyRunReport)
        assert report.qualifying_count == 0

    def test_empty_batch_is_an_explicit_empty_run(self) -> None:
        report = W.build_daily_alpha([], AS_OF)
        assert isinstance(report, EmptyRunReport)
        assert report.total_count == 0
        assert "No signals met the Daily Alpha bar" in report.reason_en
        assert "过去 24 小时" in report.reason_zh
        assert report.reason_en and report.reason_zh

    def test_item_window_excludes_stale_rows_but_history_still_scores(self) -> None:
        rows = [
            _row("FRESH", age_hours=1.0, tickers=("GEV",)),
            _row("STALE", age_hours=48.0, tickers=("PWR",)),
        ]
        draft = W.build_daily_alpha(rows, AS_OF)
        assert isinstance(draft, W.DailyAlphaDraft)
        assert [r.signal_id for r in draft.items] == ["FRESH"]
        assert "PWR" not in draft.scores  # annotations only for chosen items

    def test_future_rows_are_neither_items_nor_scores(self) -> None:
        rows = [
            _row("FRESH", age_hours=1.0, tickers=("GEV",)),
            _row("AHEAD", age_hours=-1.0, tickers=("PWR",)),
        ]
        draft = W.build_daily_alpha(rows, AS_OF)
        assert isinstance(draft, W.DailyAlphaDraft)
        assert [r.signal_id for r in draft.items] == ["FRESH"]
        assert "PWR" not in draft.scores


class TestScoreAnnotations:
    def test_scores_match_the_alpha_score_engine(self) -> None:
        rows = [_row("A", strength=4, tickers=("GEV",), category="V2_INTERCONNECTION")]
        draft = W.build_daily_alpha(rows, AS_OF)
        assert isinstance(draft, W.DailyAlphaDraft)
        events = [r.to_event() for r in draft.items]
        expected = alpha_score(events, "GEV", AS_OF).score
        delta = draft.scores["GEV"]
        assert delta.score == expected
        assert delta.delta == expected - alpha_score(
            events, "GEV", AS_OF - timedelta(days=7)
        ).score

    def test_vertical_positive_signal_scores_above_neutral(self) -> None:
        draft = W.build_daily_alpha([_row("A", strength=4)], AS_OF)
        assert isinstance(draft, W.DailyAlphaDraft)
        assert draft.scores["GEV"].score > 50.0


class TestRenderBrief:
    def test_section_72_ordering_takeaway_signals_heat_macro(self) -> None:
        draft = W.build_daily_alpha(
            [_row("A", tickers=("GEV",)), _row("B", tickers=(), theme="Data Centers")],
            AS_OF,
        )
        assert isinstance(draft, W.DailyAlphaDraft)
        markdown = verified_md(draft)
        take = markdown.index("Today in one sentence")
        sigs = markdown.index("Top signals (2)")
        heat = markdown.index("Theme heat:")
        macro = markdown.index("Macro, one line:")
        assert take < sigs < heat < macro

    def test_source_links_and_invalidation_always_visible(self) -> None:
        draft = W.build_daily_alpha([_row("A", tickers=("GEV",))], AS_OF)
        assert isinstance(draft, W.DailyAlphaDraft)
        markdown = verified_md(draft)
        assert URL in markdown  # every source link
        assert INVALIDATION in markdown  # every invalidation condition
        assert "Human approval gate" in markdown  # the §8.1 gate is noted

    def test_numeric_scenario_ranges_never_render(self) -> None:
        draft = W.build_daily_alpha(
            [_row("A", scenario_range={"bear": -0.06, "base": 0.04, "bull": 0.15})], AS_OF
        )
        assert isinstance(draft, W.DailyAlphaDraft)
        markdown = verified_md(draft)
        for banned in ("-0.06", "0.04", "0.15"):
            assert banned not in markdown

    def test_rejected_render_lists_machine_readable_reasons(self) -> None:
        draft = W.build_daily_alpha([_row("A", tickers=("GEV",))], AS_OF)
        assert isinstance(draft, W.DailyAlphaDraft)
        failed = W.verify_draft(
            draft.verification_input(),
            source_checker=StubChecker(fail_urls=frozenset({URL})),
        )
        markdown = W.render_brief(draft, failed)
        assert "REJECTED BY VERIFICATION" in markdown
        assert "`UNREACHABLE_SOURCE`" in markdown

    def test_strength_four_callout(self) -> None:
        draft = W.build_daily_alpha([_row("A", strength=4, tickers=("GEV",))], AS_OF)
        assert isinstance(draft, W.DailyAlphaDraft)
        markdown = verified_md(draft)
        assert "strength ≥ 4" in markdown


class TestStageBrief:
    def test_clean_draft_stages_pending_approval(self) -> None:
        rows = [_row("A", tickers=("GEV",)), _row("B", tickers=("PWR",), age_hours=2.0)]
        staged = W.stage_brief(W.build_daily_alpha(rows, AS_OF), source_checker=StubChecker())
        assert staged.status == W.STATUS_PENDING_APPROVAL
        assert staged.title == "[daily-alpha] 2026-11-09 — PENDING HUMAN APPROVAL"
        assert staged.verification.passed

    def test_tampered_draft_is_rejected_never_sent(self) -> None:
        rows = [_row("A", tickers=("GEV",), summary_zh="该项目的目标价已上探。")]
        staged = W.stage_brief(W.build_daily_alpha(rows, AS_OF), source_checker=StubChecker())
        assert staged.status == W.STATUS_REJECTED
        assert "REJECTED BY VERIFICATION" in staged.title
        assert not staged.verification.passed
        assert any(i.code == BANNED_LANGUAGE for i in staged.verification.issues)

    def test_unreachable_source_stages_rejection(self) -> None:
        rows = [_row("A", tickers=("GEV",))]
        staged = W.stage_brief(
            W.build_daily_alpha(rows, AS_OF),
            source_checker=StubChecker(fail_urls=frozenset({URL})),
        )
        assert staged.status == W.STATUS_REJECTED

    def test_empty_run_stages_explicitly(self) -> None:
        staged = W.stage_brief(W.build_daily_alpha([], AS_OF), source_checker=StubChecker())
        assert staged.status == W.STATUS_EMPTY_RUN
        assert "EMPTY RUN" in staged.title
        assert "No signals met the Daily Alpha bar" in staged.markdown

    def test_issue_title_unknown_status_raises(self) -> None:
        with pytest.raises(W.WriterInputError):
            W.issue_title(AS_OF, "bogus")


class TestNormalizeRow:
    def test_missing_required_field_is_loud(self) -> None:
        row = _row("A")
        del row["invalidation"]
        with pytest.raises(W.WriterInputError, match="invalidation"):
            W.normalize_row(row)

    def test_naive_published_at_rejected(self) -> None:
        row = _row("A")
        row["published_at"] = "2026-11-09T01:00:00"  # no offset — not timestamptz
        with pytest.raises(W.WriterInputError, match="timezone-aware"):
            W.normalize_row(row)

    def test_bad_strength_rejected(self) -> None:
        with pytest.raises(W.WriterInputError, match="strength"):
            W.normalize_row(_row("A", strength=9))

    def test_bad_direction_rejected(self) -> None:
        with pytest.raises(W.WriterInputError, match="direction"):
            W.normalize_row(_row("A", direction="sideways"))

    def test_sources_must_be_an_array(self) -> None:
        row = _row("A")
        row["sources"] = "https://www.pjm.com/"
        with pytest.raises(W.WriterInputError, match="sources"):
            W.normalize_row(row)

    def test_datetime_objects_accepted(self) -> None:
        row = _row("A")
        row["published_at"] = AS_OF
        assert W.normalize_row(row).published_at == AS_OF


class TestEntrypoint:
    # tests/agents/test_writer.py → pipelines/examples/
    FIXTURE = (
        Path(__file__).resolve().parents[2] / "examples" / "daily_alpha_sample_batch.json"
    )

    def test_fixture_mode_stages_pending_approval(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        code = main(
            [
                "--signals-file",
                str(self.FIXTURE),
                "--source-checker",
                "none",
                "--out-dir",
                str(out),
                "--as-of",
                "2026-11-09T11:30:00Z",
            ]
        )
        assert code == 0
        status = json.loads((out / "status.json").read_text(encoding="utf-8"))
        assert status["status"] == W.STATUS_PENDING_APPROVAL
        assert status["item_count"] == 3
        issue_md = (out / "issue.md").read_text(encoding="utf-8")
        assert "PENDING HUMAN APPROVAL" in issue_md
        assert "https://www.sec.gov/" in issue_md
        brief = json.loads((out / "brief.json").read_text(encoding="utf-8"))
        assert brief["takeaway_en"]

    def test_fixture_mode_rejects_tampered_rows(self, tmp_path: Path) -> None:
        rows = json.loads(self.FIXTURE.read_text(encoding="utf-8"))
        rows[0]["summary_zh"] = "目标价上探。"
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        out = tmp_path / "out"
        code = main(
            [
                "--signals-file",
                str(bad),
                "--source-checker",
                "none",
                "--out-dir",
                str(out),
                "--as-of",
                "2026-11-09T11:30:00Z",
            ]
        )
        assert code == 0  # a rejection is a staged, handled outcome
        status = json.loads((out / "status.json").read_text(encoding="utf-8"))
        assert status["status"] == W.STATUS_REJECTED

    def test_empty_batch_stages_empty_run(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.json"
        empty.write_text("[]", encoding="utf-8")
        out = tmp_path / "out"
        code = main(
            [
                "--signals-file",
                str(empty),
                "--source-checker",
                "none",
                "--out-dir",
                str(out),
                "--as-of",
                "2026-11-09T11:30:00Z",
            ]
        )
        assert code == 0
        status = json.loads((out / "status.json").read_text(encoding="utf-8"))
        assert status["status"] == W.STATUS_EMPTY_RUN
        assert "EMPTY RUN" in (out / "issue.md").read_text(encoding="utf-8")

    def test_missing_config_is_an_error_not_an_empty_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for var in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY"):
            monkeypatch.delenv(var, raising=False)
        code = main(["--out-dir", str(tmp_path / "out"), "--as-of", "2026-11-09T11:30:00Z"])
        assert code == 2

    def test_sample_fixture_is_canonical_shape(self) -> None:
        """The demo fixture stays loadable and brief-worthy."""
        rows = json.loads(self.FIXTURE.read_text(encoding="utf-8"))
        built = W.build_daily_alpha(rows, AS_OF)
        assert isinstance(built, W.DailyAlphaDraft)
        assert len(built.items) == 3


def test_writer_and_verifier_agree_on_brief_size() -> None:
    assert W.MAX_ITEMS == MAX_BRIEF_ITEMS
