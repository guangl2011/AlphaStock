---
name: daily-alpha-editor
description: Use when working on the bilingual Daily Alpha brief — drafting from verified signals, the EN/ZH parity contract, or staging the draft as a GitHub issue in daily-alpha.yml.
---

# A4 — Daily Alpha Editor

## Mission

Draft the bilingual (EN/ZH) Daily Alpha brief from verified signals and stage it as a GitHub issue.

## Workflow

**Trigger:** `.github/workflows/daily-alpha.yml` — schedule `30 11 * * 1-5` (11:30 UTC; 07:30 ET in summer, 06:30 ET in winter — pre-market either way) or `workflow_dispatch` with `fixture` (default `true`).

**Steps:**

1. Read the day's verified signals from the store (Supabase REST via `SUPABASE_URL` + `SUPABASE_SERVICE_KEY`), or from `examples/daily_alpha_sample_batch.json` in fixture mode.
2. Draft the brief deterministically with the Writer Agent (`pipelines/pipelines/agents/writer.py`, called from `pipelines/pipelines/agents/daily_alpha.py`, PR #8): 1–5 signal items, each with EN and ZH summaries and invalidation conditions.
3. Route every item through the Verification Agent (A3, `pipelines/pipelines/agents/verifier.py`) — `--source-checker http` live, `none` in fixture mode.
4. Stage, never publish: the workflow creates a GitHub issue labeled `daily-alpha` (label auto-created if missing) with the title from `daily-alpha-out/status.json` (`issue_title`) and the body from `daily-alpha-out/issue.md`, and uploads the artifacts (`issue.md`, `status.json`, `brief.json`).

**Local demo (deterministic, no network):**

```bash
cd pipelines
python -m pipelines.agents.daily_alpha \
  --signals-file examples/daily_alpha_sample_batch.json \
  --source-checker none \
  --out-dir daily-alpha-out
```

**Tests:** `cd pipelines && pytest` (writer + verifier run against the sample batch; no network in CI).

## Gates

- **Staging is the ceiling.** The workflow builds, verifies, and stages — it never emails, posts, or publishes anything. Publication is A7's decision on the staged issue (design doc §8.1). Do not add a delivery step.
- A missing/unreachable database **exits 2 and turns the run red** — never silently mistaken for "no signals today."
- A fully rejected batch does not skip staging: the honest rejection is what gets staged, with the machine-readable reasons.
- Bilingual parity: both EN and ZH summaries are mandatory per item (A3 enforces `MISSING_SUMMARY`); do not ship a draft with one language filled and the other stubbed.

## Verification

A reviewer checks:

- The staged issue exists with label `daily-alpha`, latest trading day, and shows verification results per item.
- A human approval decision on the issue is the only path to publication — no merged code path sends anything.
- `pipelines ruff check . && pytest` passes locally.
- The workflow run uploaded `daily-alpha-out/{issue.md,status.json,brief.json}` artifacts.
