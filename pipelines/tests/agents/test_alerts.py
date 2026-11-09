"""Weekly top-10 alerts acceptance tests (build spec verification table).

Selection precedence (curated override vs top-10 ranking), the exactly-10 cap,
the verification gate dropping banned/unreachable/no-week-signal alerts,
signal-linked ``alert_history`` rows (approved=false, sent_at=null), and the
CLI's fixture mode + staging. No test touches the network or a live LLM —
CI-safe by construction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pipelines.agents import alerts as A
from pipelines.agents.verifier import (
    BANNED_LANGUAGE,
    BRIEF_SIZE,
    UNREACHABLE_SOURCE,
)

AS_OF = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
URL = "https://www.pjm.com/"
INVALIDATION = "Invalidated if the project is withdrawn in a later scan."
SAMPLE_BATCH = Path(__file__).resolve().parents[2] / "examples" / "weekly_alert_sample_batch.json"


def _row(
    signal_id: str,
    *,
    tickers: tuple[str, ...] = ("GEV",),
    strength: int = 3,
    direction: str = "positive",
    age_hours: float = 1.0,
    category: str = "V2_INTERCONNECTION",
    summary_en: str = "PJM queue: a large load progressed to studied.",
    summary_zh: str = "PJM 并网队列：一个大负荷项目进入「研究」阶段。",
) -> dict[str, object]:
    return {
        "signal_id": signal_id,
        "category": category,
        "event_type": "status_transition",
        "tickers": list(tickers),
        "theme": "Grid & Transmission",
        "summary_en": summary_en,
        "summary_zh": summary_zh,
        "direction": direction,
        "strength": strength,
        "horizon_days": 90,
        "macro_overlay": {"rate_sensitivity": "high", "usd_sensitivity": "low"},
        "invalidation": INVALIDATION,
        "sources": [{"type": "ISO_queue", "url": URL, "retrieved_at": "2026-11-09T01:00:00Z"}],
        "published_at": (AS_OF - timedelta(hours=age_hours)).isoformat(),
        "verified": False,
    }


@dataclass(frozen=True)
class Entry:
    """Structurally satisfies alerts.CompanyProfile (keeps data import-free)."""

    ticker: str
    company: str
    category: str = "Grid Equipment & Transformers"
    themes: str = "Grid & Transmission"
    exposure_type: str = "Direct"
    thesis: str = "Covered thesis for testing."
    key_risk: str = "Test risk."
    source_url: str = "https://example.com/universe"


def _universe(*tickers: str) -> list[Entry]:
    return [Entry(ticker=t, company=f"{t} Holdings Co") for t in tickers]


class StubChecker:
    def __init__(self, *, fail_urls: frozenset[str] = frozenset()) -> None:
        self.fail_urls = fail_urls

    def reachable(self, url: str) -> bool:
        return url not in self.fail_urls


def _eleven_ticker_rows() -> list[dict[str, object]]:
    """Eleven signal-carrying tickers — exactly one must be cut by the top-10."""
    plan = {
        "VST": (4, 20),
        "CEG": (4, 30),
        "TLN": (3, 40),
        "GEV": (4, 50),
        "PWR": (3, 60),
        "ETN": (3, 70),
        "VRT": (4, 80),
        "EQIX": (3, 90),
        "DLR": (3, 100),
        "CCJ": (2, 110),
        "NVT": (1, 116),
    }
    return [
        _row(f"S-{t}", tickers=(t,), strength=s, age_hours=age)
        for t, (s, age) in plan.items()
    ]


# --- Selection -----------------------------------------------------------------


class TestSelectTop10:
    def test_selects_exactly_ten_by_score(self) -> None:
        rows = _eleven_ticker_rows()
        universe = _universe(*{r["tickers"][0] for r in rows})  # type: ignore[arg-type]
        picks = A.select_top10(rows, universe, AS_OF)

        assert len(picks) == 10
        assert "NVT" not in {p.ticker for p in picks}  # the weakest is cut
        assert all(p.source == "ranking" for p in picks)
        scores = [p.score for p in picks]
        assert scores == sorted(scores, reverse=True)
        assert [p.rank for p in picks] == list(range(1, 11))

    def test_is_deterministic_across_runs(self) -> None:
        rows = _eleven_ticker_rows()
        universe = _universe(*{r["tickers"][0] for r in rows})  # type: ignore[arg-type]
        again = [p.ticker for p in A.select_top10(rows, universe, AS_OF)]
        assert again == [p.ticker for p in A.select_top10(rows, universe, AS_OF)]

    def test_ties_break_by_ticker(self) -> None:
        rows = [_row("S-A", tickers=("BBB",)), _row("S-B", tickers=("AAA",))]
        picks = A.select_top10(rows, _universe("AAA", "BBB"), AS_OF)
        assert [p.ticker for p in picks] == ["AAA", "BBB"]

    def test_zero_signal_tickers_fill_the_ranking(self) -> None:
        """Fewer than ten signal-carrying names: the top-10 fills from the
        universe at the 50-point base — their alerts then cannot pass
        verification (no citable signal) and are dropped downstream."""
        rows = [_row("S-X", tickers=("VST",), strength=4), _row("S-Y", tickers=("CEG",))]
        picks = A.select_top10(
            rows, _universe("VST", "CEG", "AA", "BB", "CC", "DD"), AS_OF
        )
        assert [p.ticker for p in picks[:2]] == ["VST", "CEG"]
        assert all(p.score == 50.0 for p in picks[2:])


class TestWatchlistOverride:
    def test_watchlist_overrides_ranking_entirely(self) -> None:
        rows = _eleven_ticker_rows()
        universe = _universe(*{r["tickers"][0] for r in rows})  # type: ignore[arg-type]
        watchlist = [
            A.WatchlistEntry(ticker="NVT", rank=1, rationale="Owner pins the laggard."),
            A.WatchlistEntry(ticker="VRT", rank=2, rationale="Cooling order flow."),
        ]
        picks = A.select_top10(rows, universe, AS_OF, watchlist=watchlist)
        assert [p.ticker for p in picks] == ["NVT", "VRT"]  # rank order, not score order
        assert all(p.source == "watchlist" for p in picks)

    def test_override_wins_even_when_fewer_than_ten(self) -> None:
        rows = _eleven_ticker_rows()
        universe = _universe(*{r["tickers"][0] for r in rows})  # type: ignore[arg-type]
        watchlist = [A.WatchlistEntry(ticker="CCJ", rank=1, rationale="Nuclear fuel.")]
        picks = A.select_top10(rows, universe, AS_OF, watchlist=watchlist)
        assert [p.ticker for p in picks] == ["CCJ"]

    def test_unknown_watchlist_ticker_fails_loudly(self) -> None:
        with pytest.raises(A.AlertInputError, match="not in the coverage universe"):
            A.select_top10(
                _eleven_ticker_rows(),
                _universe("VST"),
                AS_OF,
                watchlist=[A.WatchlistEntry(ticker="XXXX", rank=1, rationale="typo")],
            )

    @pytest.mark.parametrize(
        "content, match",
        [
            ("ticker,rank,rationale\nVST,1,ok\nVST,2,again\n", "duplicate"),
            ("ticker,rank,rationale\nVST,one,ok\n", "not an integer"),
            ("ticker,rank,rationale\nVST,1,\n", "empty rationale"),
            ("ticker,rank,rationale\n,1,ok\n", "empty ticker"),
        ],
    )
    def test_watchlist_contract_violations_are_loud(
        self, tmp_path: Path, content: str, match: str
    ) -> None:
        path = tmp_path / "watchlist_top10.csv"
        path.write_text(content, encoding="utf-8")
        with pytest.raises(A.AlertInputError, match=match):
            A.load_top10_watchlist(path)


class TestLoadWatchlistFile:
    def test_header_only_file_means_no_override(self, tmp_path: Path) -> None:
        path = tmp_path / "watchlist_top10.csv"
        path.write_text("ticker,rank,rationale\n", encoding="utf-8")
        assert A.load_top10_watchlist(path) == []

    def test_more_than_ten_rows_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "watchlist_top10.csv"
        path.write_text(
            "ticker,rank,rationale\n"
            + "".join(f"T{i:02d},{i},pinned {i}\n" for i in range(1, 12)),
            encoding="utf-8",
        )
        with pytest.raises(A.AlertInputError, match="caps at 10"):
            A.load_top10_watchlist(path)

    def test_missing_file_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(A.AlertInputError, match="not found"):
            A.load_top10_watchlist(tmp_path / "absent.csv")


# --- Brief building -------------------------------------------------------------


class TestDrivingSignals:
    def test_window_order_and_cap(self) -> None:
        fills = [
            _row(f"W-FILL-{i}", tickers=("GEV",), strength=2, age_hours=10 + i)
            for i in range(6)
        ]
        rows = [
            _row("OLD-STRONG", tickers=("GEV",), strength=5, age_hours=24 * 20),  # outside 7d
            _row("W-MID", tickers=("GEV",), strength=3, age_hours=48),
            _row("W-TOP", tickers=("GEV",), strength=4, age_hours=24),
            _row("OTHER", tickers=("CEG",), strength=5, age_hours=1),
            *fills,
        ]
        driving = A.driving_signals(rows, "GEV", AS_OF)
        ids = [r.signal_id for r in driving]
        assert "OLD-STRONG" not in ids and "OTHER" not in ids
        assert ids[0] == "W-TOP"  # strength first...
        assert ids[1] == "W-MID"  # ...then recency
        assert len(ids) == 5  # capped at the brief size


class TestBuildAlerts:
    def test_every_selection_gets_a_draft_with_scenario(self) -> None:
        run = A.build_alerts(_eleven_ticker_rows(), _universe("VST", "CEG"), AS_OF)
        assert run.empty_report is None
        assert len(run.drafts) == len(run.selections)
        positive = next(d for d in run.drafts if d.selection.ticker == "VST")
        assert "Constructive" in positive.scenario_en and "偏正面" in positive.scenario_zh

    def test_negative_week_reads_cautious(self) -> None:
        rows = [_row("S-N", tickers=("NVT",), direction="negative", strength=2)]
        run = A.build_alerts(rows, _universe("NVT"), AS_OF)
        draft = run.drafts[0]
        assert "Cautious" in draft.scenario_en and "偏谨慎" in draft.scenario_zh

    def test_zero_signal_ticker_reads_neutral(self) -> None:
        rows = [_row("S-X", tickers=("VST",))]
        run = A.build_alerts(rows, _universe("VST", "CEG"), AS_OF)
        draft = next(d for d in run.drafts if d.selection.ticker == "CEG")
        assert "Neutral" in draft.scenario_en and "中性" in draft.scenario_zh

    def test_alert_id_is_deterministic_per_date_and_ticker(self) -> None:
        run = A.build_alerts([_row("S-X", tickers=("VST",))], _universe("VST"), AS_OF)
        draft = run.drafts[0]
        assert draft.alert_id == "alert-2026-11-09-VST"
        again = A.build_alerts([_row("S-X", tickers=("VST",))], _universe("VST"), AS_OF)
        assert again.drafts[0].alert_id == draft.alert_id

    def test_zero_rows_is_an_explicit_empty_run(self) -> None:
        run = A.build_alerts([], _universe("VST"), AS_OF)
        assert run.signal_count == 0
        assert run.empty_report is not None
        assert "empty" in run.empty_report.reason_en.lower()


# --- Verification staging ---------------------------------------------------------


class TestStageAlerts:
    def test_all_pass_records_history_never_sends(self) -> None:
        rows = _eleven_ticker_rows()
        universe = _universe(*{r["tickers"][0] for r in rows})  # type: ignore[arg-type]
        run = A.build_alerts(rows, universe, AS_OF)
        staged = A.stage_alerts(run, source_checker=StubChecker())

        assert staged.status == A.STATUS_PENDING_APPROVAL
        assert "PENDING HUMAN APPROVAL" in staged.title
        assert len(staged.history_rows) == 10
        for row in staged.history_rows:
            assert row.approved is False
            assert row.sent_at is None
            assert row.signal_ids, "history rows must link their driving signals"

    def test_banned_language_drops_the_alert_and_logs_reasons(self) -> None:
        rows = _eleven_ticker_rows()
        rows[0] = _row(  # VST — a top-scorer, poisoned
            "S-BAD",
            tickers=("VST",),
            strength=4,
            summary_en="PJM queue: a large load progressed to studied, supporting our "
            "target price of $500.",
        )
        universe = _universe(*{r["tickers"][0] for r in rows})  # type: ignore[arg-type]
        run = A.build_alerts(rows, universe, AS_OF)
        staged = A.stage_alerts(run, source_checker=StubChecker())

        assert staged.status == A.STATUS_PENDING_APPROVAL  # mixed run still stages
        assert len(staged.history_rows) == 9  # everyone but the dropped one
        dropped = [a for a in staged.alerts if not a.passed]
        assert [a.draft.selection.ticker for a in dropped] == ["VST"]
        assert BANNED_LANGUAGE in dropped[0].issues[0].code
        # dropped = no history row, and the reasons render on the staged issue
        assert "VST" not in {r.ticker for r in staged.history_rows}
        assert BANNED_LANGUAGE in staged.markdown

    def test_unreachable_source_drops_every_alert(self) -> None:
        run = A.build_alerts(_eleven_ticker_rows(), _universe("VST"), AS_OF)
        staged = A.stage_alerts(run, source_checker=StubChecker(fail_urls=frozenset({URL})))

        assert staged.status == A.STATUS_REJECTED
        assert "REJECTED BY VERIFICATION" in staged.title
        assert staged.history_rows == ()
        assert all(UNREACHABLE_SOURCE in a.issues[0].code for a in staged.alerts)

    def test_selected_company_without_week_signals_is_dropped(self) -> None:
        """Ranks top-10 on 90-day momentum, but cites nothing from this week —
        BRIEF_SIZE (no source-linked claim), logged, never recorded."""
        rows = [_row("S-OLD", tickers=("GEV",), strength=5, age_hours=24 * 20)]
        run = A.build_alerts(rows, _universe("GEV"), AS_OF)
        staged = A.stage_alerts(run, source_checker=StubChecker())

        assert staged.status == A.STATUS_REJECTED
        assert staged.alerts[0].issues[0].code == BRIEF_SIZE
        assert staged.history_rows == ()

    def test_strength_four_callout_lists_signal_ids(self) -> None:
        rows = _eleven_ticker_rows()  # VST/CEG/GEV/VRT carry strength 4
        universe = _universe(*{r["tickers"][0] for r in rows})  # type: ignore[arg-type]
        run = A.build_alerts(rows, universe, AS_OF)
        staged = A.stage_alerts(run, source_checker=StubChecker())
        assert "strength ≥ 4" in staged.markdown
        assert "S-VST" in staged.markdown

    def test_empty_run_stages_honestly(self) -> None:
        run = A.build_alerts([], _universe("VST"), AS_OF)
        staged = A.stage_alerts(run, source_checker=StubChecker())

        assert staged.status == A.STATUS_EMPTY_RUN
        assert "EMPTY RUN" in staged.title
        assert "空报告" in staged.markdown
        assert staged.history_rows == ()


# --- alert_history persistence -----------------------------------------------------


class _StubConn:
    def __init__(self, *, fail_on: int | None = None) -> None:
        self.executed: list[tuple[str, tuple[object, ...] | None]] = []
        self.committed = False
        self.rolled_back = False
        self.fail_on = fail_on

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        if self.fail_on is not None and len(self.executed) == self.fail_on:
            raise RuntimeError("connection lost mid-batch")
        self.executed.append((sql, params))

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


class TestPublishAlertHistory:
    def test_inserts_commits_and_returns_ids(self) -> None:
        run = A.build_alerts([_row("S-X", tickers=("VST",))], _universe("VST"), AS_OF)
        staged = A.stage_alerts(run, source_checker=StubChecker())
        conn = _StubConn()

        recorded = A.publish_alert_history(conn, staged.history_rows)

        assert recorded == [r.alert_id for r in staged.history_rows]
        assert len(conn.executed) == len(staged.history_rows)
        assert conn.committed and not conn.rolled_back
        sql, params = conn.executed[0]
        assert "INSERT INTO alert_history" in sql
        assert "ON CONFLICT (alert_id) DO NOTHING" in sql
        assert params is not None and params[8] is False and params[9] is None

    def test_failure_rolls_back_the_whole_batch(self) -> None:
        rows = _eleven_ticker_rows()
        universe = _universe(*{r["tickers"][0] for r in rows})  # type: ignore[arg-type]
        run = A.build_alerts(rows, universe, AS_OF)
        staged = A.stage_alerts(run, source_checker=StubChecker())
        conn = _StubConn(fail_on=3)

        with pytest.raises(RuntimeError, match="connection lost"):
            A.publish_alert_history(conn, staged.history_rows)
        assert conn.rolled_back and not conn.committed

    def test_empty_batch_writes_nothing(self) -> None:
        conn = _StubConn()
        assert A.publish_alert_history(conn, []) == []
        assert conn.executed == []


# --- CLI (fixture mode, no network) -------------------------------------------------


class TestCli:
    def test_fixture_run_stages_pending_approval(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        code = A.main(
            [
                "--signals-file",
                str(SAMPLE_BATCH),
                "--source-checker",
                "none",
                "--as-of",
                "2026-11-09T12:00:00Z",
                "--out-dir",
                str(out),
            ]
        )

        assert code == 0
        status = json.loads((out / "status.json").read_text(encoding="utf-8"))
        assert status["status"] == A.STATUS_PENDING_APPROVAL
        assert "PENDING HUMAN APPROVAL" in status["issue_title"]
        assert status["alert_count"] == 10
        assert status["passed"] == 10
        assert status["history_recorded"] == []  # nothing published without --publish-history
        issue_md = (out / "issue.md").read_text(encoding="utf-8")
        assert "PENDING APPROVAL" in issue_md  # body renders the status line
        assert "每周警报（中文）" in issue_md
        history = json.loads((out / "history_rows.json").read_text(encoding="utf-8"))
        assert len(history) == 10
        assert all(row["approved"] is False and row["sent_at"] is None for row in history)

    def test_publish_history_requires_db_url(self, tmp_path: Path) -> None:
        code = A.main(
            [
                "--signals-file",
                str(SAMPLE_BATCH),
                "--source-checker",
                "none",
                "--as-of",
                "2026-11-09T12:00:00Z",
                "--out-dir",
                str(tmp_path / "out"),
                "--publish-history",
            ]
        )
        assert code == 2

    def test_naive_as_of_rejected(self, tmp_path: Path) -> None:
        code = A.main(
            [
                "--signals-file",
                str(SAMPLE_BATCH),
                "--source-checker",
                "none",
                "--as-of",
                "2026-11-09T12:00:00",  # no timezone
                "--out-dir",
                str(tmp_path / "out"),
            ]
        )
        assert code == 2

    def test_live_mode_without_credentials_fails_loud(self, tmp_path: Path) -> None:
        code = A.main(["--out-dir", str(tmp_path / "out")])
        assert code == 2

    def test_no_send_path_in_module(self) -> None:
        """Tripwire: the alert builder must never grow a delivery path."""
        source = Path(A.__file__).read_text(encoding="utf-8")
        for banned in ("smtplib", "sendmail", "slack", "SMTP", "requests.post"):
            assert banned not in source, f"alerts module must not contain {banned!r}"
