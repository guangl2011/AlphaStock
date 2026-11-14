---
name: verification-gate
description: Use when touching the deterministic verification gate — banned-language checks, source-link requirements, bilingual parity, or brief shape — or when wiring any draft (Daily Alpha or weekly alerts) through verification.
tools: Read, Grep, Glob, Bash
---

> **Twin contract:** `.obvious/skills/verification-gate/SKILL.md` is the source of truth.
> Keep this file's Mission / Workflow / Gates / Verification in sync with it in the same PR (see root `CLAUDE.md`, Contract parity).

# A3 — Verification

## Mission

Fact-check every draft signal and brief: tickers, figures, dates, sourcing, and bilingual copy parity.

## Workflow

**Trigger:** any drafting run — `daily-alpha.yml` and `weekly-alerts.yml` both route their drafts through this gate inside the same workflow run (PR #8). There is no standalone schedule.

**Steps:**

1. Run the deterministic checks in `pipelines/pipelines/agents/verifier.py` against every drafted item:
   - **Banned-language filter (EN + ZH):** the six spec patterns, matched case-insensitively — `预期收益`, `目标价`, `保证`, `target price`, `expected return`, `guaranteed` → `BANNED_LANGUAGE`.
   - **Source-linked claims:** every item carries at least one reachable `http(s)` source → else `MISSING_SOURCE` / `INVALID_SOURCE_URL` / `UNREACHABLE_SOURCE`.
   - **Invalidation conditions + both language summaries present** → else `MISSING_INVALIDATION` / `MISSING_SUMMARY`.
   - **Brief shape (§7.2):** 1–5 signal items (`MIN_BRIEF_ITEMS=1`, `MAX_BRIEF_ITEMS=5`) → else `BRIEF_SIZE`.
2. Inject the source checker explicitly — `--source-checker http` in live runs, `--source-checker none` in fixture/unit-test runs. The checker is never assumed; CI stubs it so no test touches the network.
3. Render machine-readable issue codes verbatim on the staged issue so a reader can audit each rejection reason.
4. Per-number semantic verification against source page content is the runtime LLM stage's job — this gate guarantees the structural properties, not semantics.

**Tests:** `cd pipelines && pytest` — verifier tests cover each issue code with stub checkers.

## Gates

- **A failed check returns the item to the drafting agent** with its machine-readable reasons. Nothing unverified reaches a human queue — a rejected batch stages a `REJECTED BY VERIFICATION` issue rather than skipping verification.
- The gate is deterministic-first: never replace these checks with an LLM judgment call, and never add a path that publishes without a passing gate result.
- The source checker is injected, never assumed — an un-injected checker is a bug, not a default.
- Rejection reasons are append-only evidence; do not edit them out of a staged issue.

## Verification

A reviewer checks:

- Every staged issue (label `daily-alpha` or `weekly-alert`) shows the verification pass — or shows explicit rejection codes per item; no issue lacks a verification record.
- `pipelines ruff check . && pytest` passes, including the banned-language and brief-shape cases.
- Grep the drafting modules for any call path that skips the verifier: `grep -rn "verify" pipelines/pipelines/agents/daily_alpha.py pipelines/pipelines/agents/alerts.py` — verification is in-line in both.

## Where this lives in the repo

**Repo surfaces:** `.github/workflows/daily-alpha.yml` and `.github/workflows/weekly-alerts.yml` (which route drafts through this gate); `pipelines/pipelines/agents/verifier.py`.
