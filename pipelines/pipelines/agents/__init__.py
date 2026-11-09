"""LLM agent stages as deterministic pipeline steps (design doc §8.1).

The 4+1 architecture runs in-pipeline, not as a runtime service: each stage
is a pure, fixture-testable function, and CI never calls a live LLM. The
Verification Agent (:mod:`pipelines.agents.verifier`) is the publication
gate; the Writer (:mod:`pipelines.agents.writer`) renders the bilingual
Daily Alpha and stages it — it never sends.
"""

from .verifier import (
    BANNED_LANGUAGE,
    BANNED_PATTERNS,
    BRIEF_SIZE,
    INVALID_SOURCE_URL,
    MAX_BRIEF_ITEMS,
    MIN_BRIEF_ITEMS,
    MISSING_INVALIDATION,
    MISSING_SOURCE,
    MISSING_SUMMARY,
    UNREACHABLE_SOURCE,
    HttpSourceChecker,
    SourceChecker,
    SourceRef,
    VerifiableDraft,
    VerifiableItem,
    VerificationIssue,
    VerificationResult,
    verify_draft,
    verify_empty_run,
)

__all__ = [
    "BANNED_LANGUAGE",
    "BANNED_PATTERNS",
    "BRIEF_SIZE",
    "HttpSourceChecker",
    "INVALID_SOURCE_URL",
    "MAX_BRIEF_ITEMS",
    "MIN_BRIEF_ITEMS",
    "MISSING_INVALIDATION",
    "MISSING_SOURCE",
    "MISSING_SUMMARY",
    "SourceChecker",
    "SourceRef",
    "UNREACHABLE_SOURCE",
    "VerifiableDraft",
    "VerifiableItem",
    "VerificationIssue",
    "VerificationResult",
    "verify_draft",
    "verify_empty_run",
]
