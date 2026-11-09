"""Verification Agent acceptance tests (build spec verification table).

Covers exactly the spec'd behaviors: banned EN/ZH patterns flag violations,
a draft citing an unreachable or missing source fails, clean drafts pass,
and every rejection is machine-readable (code + field + detail). No test
touches the network — reachability is always a stub.
"""

from __future__ import annotations

import urllib.error
import urllib.request

import pytest

from pipelines.agents import (
    BANNED_LANGUAGE,
    BANNED_PATTERNS,
    BRIEF_SIZE,
    INVALID_SOURCE_URL,
    MAX_BRIEF_ITEMS,
    MISSING_INVALIDATION,
    MISSING_SOURCE,
    MISSING_SUMMARY,
    UNREACHABLE_SOURCE,
    HttpSourceChecker,
    SourceRef,
    VerifiableDraft,
    VerifiableItem,
    verify_draft,
    verify_empty_run,
)

URL = "https://www.pjm.com/"


class StubChecker:
    """Reachability oracle with no network: per-URL failures, default pass."""

    def __init__(self, *, fail_urls: frozenset[str] = frozenset()) -> None:
        self.fail_urls = fail_urls

    def reachable(self, url: str) -> bool:
        return url not in self.fail_urls


def item(
    *,
    label: str = "SIG-2026-11-09-000001",
    summary_en: str = "PJM queue: a large load progressed to studied.",
    summary_zh: str = "PJM 并网队列：一个大负荷项目进入「已研究」阶段。",
    invalidation: str = "Invalidated if the project is withdrawn in a later scan.",
    sources: tuple[SourceRef, ...] = (SourceRef(url=URL, type="ISO_queue"),),
) -> VerifiableItem:
    return VerifiableItem(
        label=label,
        summary_en=summary_en,
        summary_zh=summary_zh,
        invalidation=invalidation,
        sources=sources,
    )


def draft(
    items: tuple[VerifiableItem, ...] | None = None,
    fields: dict[str, str] | None = None,
) -> VerifiableDraft:
    return VerifiableDraft(
        fields=fields
        if fields is not None
        else {
            "title_en": "Daily Alpha · 2026-11-09",
            "title_zh": "每日信号 · 2026-11-09",
            "takeaway_en": "2 signals cleared the bar today, net constructive.",
            "takeaway_zh": "今日 2 条信号过线，整体偏正面。",
            "macro_en": "Rate sensitivity across today's signals: high.",
            "macro_zh": "今日信号利率敏感度：高。",
        },
        items=items if items is not None else (item(), item(label="SIG-…-000002")),
    )


def verify_ok(d: VerifiableDraft) -> bool:
    return verify_draft(d, source_checker=StubChecker()).passed


class TestBannedLanguage:
    @pytest.mark.parametrize(
        "pattern", ["预期收益", "目标价", "保证"], ids=["shouyi", "mubiaojia", "baozheng"]
    )
    def test_zh_patterns_in_zh_summary(self, pattern: str) -> None:
        bad = item(summary_zh=f"该项目的预期收益为 {pattern} 信号。")
        result = verify_draft(draft((bad, item())), source_checker=StubChecker())
        assert not result.passed
        assert BANNED_LANGUAGE in result.reason_codes()
        flagged = [i for i in result.issues if i.code == BANNED_LANGUAGE]
        assert any("summary_zh" in i.field and pattern in i.detail for i in flagged)

    @pytest.mark.parametrize(
        "pattern", ["target price", "expected return", "guaranteed"]
    )
    def test_en_patterns_in_en_summary(self, pattern: str) -> None:
        bad = item(summary_en=f"This move {pattern} upside for the theme.")
        result = verify_draft(draft((bad, item())), source_checker=StubChecker())
        assert not result.passed
        assert BANNED_LANGUAGE in result.reason_codes()

    def test_case_insensitive(self) -> None:
        bad = item(summary_en="We view this as a Target Price candidate.")
        result = verify_draft(draft((bad, item())), source_checker=StubChecker())
        assert not result.passed

    def test_pattern_in_free_text_field_is_flagged(self) -> None:
        result = verify_draft(
            draft(fields={"macro_en": "a guaranteed setup", "macro_zh": "保证兑现"}),
            source_checker=StubChecker(),
        )
        assert not result.passed
        codes = result.reason_codes()
        assert codes.count(BANNED_LANGUAGE) == 2

    def test_full_list_is_the_spec_six(self) -> None:
        assert BANNED_PATTERNS == (
            "预期收益",
            "目标价",
            "保证",
            "target price",
            "expected return",
            "guaranteed",
        )


class TestSourceLinking:
    def test_no_sources_fails(self) -> None:
        bad = item(sources=())
        result = verify_draft(draft((bad, item())), source_checker=StubChecker())
        assert not result.passed
        assert MISSING_SOURCE in result.reason_codes()

    def test_blank_source_url_fails(self) -> None:
        bad = item(sources=(SourceRef(url="   "),))
        result = verify_draft(draft((bad, item())), source_checker=StubChecker())
        assert not result.passed
        assert MISSING_SOURCE in result.reason_codes()

    def test_non_http_url_fails(self) -> None:
        bad = item(sources=(SourceRef(url="ftp://queues.example.com/data.csv"),))
        result = verify_draft(draft((bad, item())), source_checker=StubChecker())
        assert not result.passed
        assert INVALID_SOURCE_URL in result.reason_codes()

    def test_unreachable_source_fails(self) -> None:
        dead = "https://queues.example.com/removed-report"
        bad = item(sources=(SourceRef(url=dead),))
        result = verify_draft(
            draft((bad, item())), source_checker=StubChecker(fail_urls=frozenset({dead}))
        )
        assert not result.passed
        assert UNREACHABLE_SOURCE in result.reason_codes()
        issue = next(i for i in result.issues if i.code == UNREACHABLE_SOURCE)
        assert dead in issue.detail

    def test_reachable_source_passes(self) -> None:
        assert verify_ok(draft())


class TestStructureAndCleanPass:
    def test_zero_items_is_a_brief_size_violation(self) -> None:
        result = verify_draft(draft(items=()), source_checker=StubChecker())
        assert not result.passed
        assert BRIEF_SIZE in result.reason_codes()

    def test_over_five_items_is_a_brief_size_violation(self) -> None:
        items = tuple(item(label=f"SIG-{n:08d}") for n in range(MAX_BRIEF_ITEMS + 1))
        result = verify_draft(draft(items), source_checker=StubChecker())
        assert not result.passed
        assert BRIEF_SIZE in result.reason_codes()

    def test_blank_summary_fails(self) -> None:
        bad = item(summary_en="   ")
        result = verify_draft(draft((bad, item())), source_checker=StubChecker())
        assert not result.passed
        assert MISSING_SUMMARY in result.reason_codes()

    def test_missing_invalidation_fails(self) -> None:
        bad = item(invalidation="")
        result = verify_draft(draft((bad, item())), source_checker=StubChecker())
        assert not result.passed
        assert MISSING_INVALIDATION in result.reason_codes()

    def test_clean_draft_passes_with_no_issues(self) -> None:
        result = verify_draft(draft(), source_checker=StubChecker())
        assert result.passed
        assert result.issues == ()
        assert result.reason_codes() == ()

    def test_rejection_reasons_are_machine_readable(self) -> None:
        bad = item(summary_zh="目标价上探。", sources=())
        result = verify_draft(draft((bad, item())), source_checker=StubChecker())
        assert not result.passed
        for issue in result.issues:
            assert issue.code and issue.field and issue.detail


class TestEmptyRun:
    def test_clean_reasons_pass(self) -> None:
        result = verify_empty_run(
            "No signals met the Daily Alpha bar in the last 24 hours.",
            "过去 24 小时内没有达到 Daily Alpha 门槛的信号。",
        )
        assert result.passed

    def test_blank_reason_fails(self) -> None:
        result = verify_empty_run("   ", "有理由。")
        assert not result.passed
        assert MISSING_SUMMARY in result.reason_codes()

    def test_banned_pattern_in_reason_fails(self) -> None:
        result = verify_empty_run("Nothing to report, guaranteed.", "没有信号。")
        assert not result.passed
        assert BANNED_LANGUAGE in result.reason_codes()


class TestHttpSourceChecker:
    """Status classification only — urlopen is patched, never dialed."""

    class _FakeResponse:
        def __init__(self, status: int) -> None:
            self.status = status

        def __enter__(self) -> TestHttpSourceChecker._FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def _patch(self, monkeypatch: pytest.MonkeyPatch, behavior: object) -> None:
        monkeypatch.setattr(urllib.request, "urlopen", behavior)

    def test_2xx_is_reachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch(monkeypatch, lambda req, timeout: self._FakeResponse(200))
        assert HttpSourceChecker().reachable(URL) is True

    def test_bot_blocked_is_reachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch(monkeypatch, lambda req, timeout: self._FakeResponse(403))
        assert HttpSourceChecker().reachable(URL) is True

    def test_404_is_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch(monkeypatch, lambda req, timeout: self._FakeResponse(404))
        assert HttpSourceChecker().reachable(URL) is False

    def test_http_error_status_is_classified(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def raise_http_error(req: object, timeout: object) -> object:
            raise urllib.error.HTTPError(URL, 410, "Gone", None, None)  # type: ignore[arg-type]

        self._patch(monkeypatch, raise_http_error)
        assert HttpSourceChecker().reachable(URL) is False

    def test_transport_failure_is_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def raise_url_error(req: object, timeout: object) -> object:
            raise urllib.error.URLError("name resolution failed")

        self._patch(monkeypatch, raise_url_error)
        assert HttpSourceChecker().reachable(URL) is False
