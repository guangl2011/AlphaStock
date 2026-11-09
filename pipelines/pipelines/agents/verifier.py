"""Verification Agent: the deterministic compliance gate (design doc §8.1 stage 5).

Nothing publishes without passing this gate (build spec named failure case:
"verification rejects a draft → nothing publishes; reasons are
machine-readable"). The MVP is deterministic-first by design — the checks
below are the ones that must never depend on a model's good behavior:

- banned-language filter (EN + ZH) across every drafted text field;
- every claim source-linked: each item carries at least one reachable,
  http(s) source;
- mandatory invalidation conditions and both language summaries present;
- §7.2 brief shape: between one and five signal items.

Per-number semantic verification against the content of a source page is the
runtime LLM stage's job (design doc §8.1); this gate guarantees the
structural properties that let a reader audit every claim themselves.

The source checker is injected, never assumed: CI and unit tests pass a
stub, so no test ever touches the network.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

#: Banned-language list from the MVP build spec (design doc §11 收益承诺
#: filter). Matched case-insensitively as substrings; ZH entries match their
#: own script. Exactly the six patterns the spec names.
BANNED_PATTERNS: tuple[str, ...] = (
    "预期收益",
    "目标价",
    "保证",
    "target price",
    "expected return",
    "guaranteed",
)

#: §7.2 brief shape: 3–5 signals is the spec's brief. Fewer than one
#: qualifying signal is an explicit empty run (reported by the writer), never
#: a zero-item draft; more than five is a padded brief.
MIN_BRIEF_ITEMS = 1
MAX_BRIEF_ITEMS = 5

# Machine-readable issue codes — rendered verbatim on the staged issue.
BANNED_LANGUAGE = "BANNED_LANGUAGE"
MISSING_SOURCE = "MISSING_SOURCE"
INVALID_SOURCE_URL = "INVALID_SOURCE_URL"
UNREACHABLE_SOURCE = "UNREACHABLE_SOURCE"
MISSING_INVALIDATION = "MISSING_INVALIDATION"
MISSING_SUMMARY = "MISSING_SUMMARY"
BRIEF_SIZE = "BRIEF_SIZE"


@dataclass(frozen=True)
class SourceRef:
    """One cited source on a draft item (subset of a row's sources entry)."""

    url: str
    type: str = "unknown"


@dataclass(frozen=True)
class VerifiableItem:
    """One drafted signal claim as the verifier sees it."""

    label: str  # e.g. the signal_id, used in issue details
    summary_en: str
    summary_zh: str
    invalidation: str
    sources: tuple[SourceRef, ...]


@dataclass(frozen=True)
class VerifiableDraft:
    """The text surface of a draft: scanned free-text fields plus items."""

    fields: Mapping[str, str]  # field name -> drafted text (title, takeaway, macro)
    items: tuple[VerifiableItem, ...]


@dataclass(frozen=True)
class VerificationIssue:
    """Machine-readable rejection reason; rendered verbatim on the staged issue."""

    code: str
    field: str
    detail: str


@dataclass(frozen=True)
class VerificationResult:
    passed: bool
    issues: tuple[VerificationIssue, ...]

    def reason_codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)


class SourceChecker(Protocol):
    """Reachability oracle. Tests inject stubs; runtime uses ``HttpSourceChecker``."""

    def reachable(self, url: str) -> bool: ...


class HttpSourceChecker:
    """Runtime source checker: GET the URL, judge by response status.

    A server that answers but refuses this client (401/403/405/429) counts as
    reachable — a bot block is not evidence of a missing source. 404/410,
    other error statuses, and transport failures are unreachable or missing.
    """

    _BOT_BLOCKED = frozenset({401, 403, 405, 406, 429})

    def __init__(self, timeout_s: float = 10.0) -> None:
        self._timeout_s = timeout_s

    def reachable(self, url: str) -> bool:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "AlphaStockVerifier/0.1 (+https://github.com/guangl2011/AlphaStock)"
                ),
                "Range": "bytes=0-0",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as resp:
                status = resp.status
        except urllib.error.HTTPError as err:
            status = err.code
        except (urllib.error.URLError, OSError, ValueError):
            return False
        return status < 400 or status in self._BOT_BLOCKED


def _scan_banned(fields: Mapping[str, str]) -> tuple[VerificationIssue, ...]:
    issues: list[VerificationIssue] = []
    for field in sorted(fields):
        lowered = fields[field].lower()
        for pattern in BANNED_PATTERNS:
            if pattern in lowered:
                issues.append(
                    VerificationIssue(
                        code=BANNED_LANGUAGE,
                        field=field,
                        detail=f'banned pattern "{pattern}" (compliance list)',
                    )
                )
    return tuple(issues)


def _check_item_sources(
    item: VerifiableItem, index: int, source_checker: SourceChecker
) -> tuple[VerificationIssue, ...]:
    issues: list[VerificationIssue] = []
    if not item.sources:
        issues.append(
            VerificationIssue(
                code=MISSING_SOURCE,
                field=f"items[{index}]",
                detail=(
                    f"{item.label}: no sources cited — every claim must be source-linked"
                ),
            )
        )
    for position, source in enumerate(item.sources):
        field = f"items[{index}].sources[{position}]"
        url = source.url.strip()
        if not url:
            issues.append(
                VerificationIssue(
                    code=MISSING_SOURCE, field=field, detail=f"{item.label}: blank source url"
                )
            )
            continue
        if not (url.startswith("http://") or url.startswith("https://")):
            issues.append(
                VerificationIssue(
                    code=INVALID_SOURCE_URL,
                    field=field,
                    detail=f"{item.label}: non-http(s) source url {url!r}",
                )
            )
            continue
        if not source_checker.reachable(url):
            issues.append(
                VerificationIssue(
                    code=UNREACHABLE_SOURCE,
                    field=field,
                    detail=f"{item.label}: source did not resolve or is missing: {url}",
                )
            )
    return tuple(issues)


def verify_draft(
    draft: VerifiableDraft, *, source_checker: SourceChecker
) -> VerificationResult:
    """Gate a Daily Alpha draft. Failing drafts never publish or send."""
    issues: list[VerificationIssue] = list(_scan_banned(draft.fields))

    for index, item in enumerate(draft.items):
        issues.extend(
            _scan_banned(
                {
                    f"items[{index}].summary_en": item.summary_en,
                    f"items[{index}].summary_zh": item.summary_zh,
                    f"items[{index}].invalidation": item.invalidation,
                }
            )
        )
        for field, text in (
            (f"items[{index}].summary_en", item.summary_en),
            (f"items[{index}].summary_zh", item.summary_zh),
        ):
            if not text or not text.strip():
                issues.append(
                    VerificationIssue(
                        code=MISSING_SUMMARY,
                        field=field,
                        detail=f"{item.label}: summary is blank",
                    )
                )
        if not item.invalidation or not item.invalidation.strip():
            issues.append(
                VerificationIssue(
                    code=MISSING_INVALIDATION,
                    field=f"items[{index}].invalidation",
                    detail=f"{item.label}: invalidation condition is mandatory (design doc §4.2)",
                )
            )
        issues.extend(_check_item_sources(item, index, source_checker))

    if not MIN_BRIEF_ITEMS <= len(draft.items) <= MAX_BRIEF_ITEMS:
        issues.append(
            VerificationIssue(
                code=BRIEF_SIZE,
                field="items",
                detail=(
                    f"{len(draft.items)} items — the §7.2 brief carries "
                    f"{MIN_BRIEF_ITEMS}-{MAX_BRIEF_ITEMS} signals; zero qualifying signals "
                    "is an explicit empty run, not a draft"
                ),
            )
        )

    return VerificationResult(passed=not issues, issues=tuple(issues))


def verify_empty_run(reason_en: str, reason_zh: str) -> VerificationResult:
    """Gate an empty-run report: banned language and explicitness only.

    An empty run cites no claims and no sources; what it must never be is
    padded or vague, so the check is: reasons present in both languages and
    free of banned patterns.
    """
    issues: list[VerificationIssue] = list(
        _scan_banned({"reason_en": reason_en, "reason_zh": reason_zh})
    )
    if not reason_en.strip() or not reason_zh.strip():
        issues.append(
            VerificationIssue(
                code=MISSING_SUMMARY,
                field="reason",
                detail="empty-run report must state its reason in both languages",
            )
        )
    return VerificationResult(passed=not issues, issues=tuple(issues))
