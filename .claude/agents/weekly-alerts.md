---
name: weekly-alerts
description: Use when working on the weekly top-10 alert builder — Alpha Score ranking, watchlist override, bilingual alert drafting with verification, alert_history recording, or the Monday 12:00 UTC staging workflow.
tools: Read, Grep, Glob, Bash
---

> **Twin contract:** `.obvious/skills/weekly-alerts/SKILL.md` is the source of truth.
> Keep this file's Mission / Workflow / Gates / Verification in sync with it in the same PR (see root `CLAUDE.md`, Contract parity).

# A6 — Weekly Alerts

## Mission

Build the weekly top-10 alert list from verified signals and stage it Monday 12:00 UTC for approval.

## Workflow

**Trigger:** `.github/workflows/weekly-alerts.yml` — schedule `0 12 * * 1` (Monday 12:00 UTC) or `workflow_dispatch` with `fixture` (default `true`), optional `as_of`, and `publish_history` (default `false`).

**Steps:**

1. Read the merged weekly-scan signals **from the store** (Supabase REST) — this workflow never re-runs the scan.
2. Select the top 10 covered companies by current Alpha Score (`pipelines/data/universe.csv` ranking; a non-empty `pipelines/data/watchlist_top10.csv` overrides the ranking entirely), via `pipelines/pipelines/agents/alerts.py` (PR #12).
3. Draft one bilingual alert per company and route each through the same Verification Agent as Daily Alpha (A3, `pipelines/pipelines/agents/verifier.py`) — `--source-checker http` live, `none` in fixture mode.
4. Record passing alerts in `alert_history` when `publish_history` is set (Supabase runs only).
5. Stage the whole run as one GitHub issue labeled `weekly-alert` (label auto-created if missing) — including the honest outcomes: an empty store stages an `EMPTY RUN` issue; a fully rejected batch stages a `REJECTED BY VERIFICATION` issue.

**Local demo (deterministic, no network):**

```bash
cd pipelines
python -m pipelines.agents.alerts \
  --signals-file examples/weekly_alert_sample_batch.json \
  --source-checker none \
  --out-dir weekly-alerts-out
```

**Tests:** `cd pipelines && pytest` (selection, drafting, and verification run against the sample batch).

## Gates

- **No auto-send path exists — and none may be added.** The workflow builds, verifies, records, and stages; approval happens on the staged issue (design doc §8.1). See root `obvious.md`, rule 2.
- A missing/unreachable database **exits 2 and turns the run red** — never mistaken for "no signals."
- Empty and rejected runs stage honest outcomes instead of failing silently; only infrastructure failure turns the workflow red.
- Bilingual parity per alert is enforced by the verification gate (A3); do not draft single-language alerts.

## Verification

A reviewer checks:

- The staged issue exists with label `weekly-alert`, dated this Monday, listing the top-10 selection with per-item verification results.
- `alert_history` rows exist for passing alerts when `publish_history` was set (append-only store, PR #3 schema).
- If the watchlist override applied, `pipelines/data/watchlist_top10.csv` is non-empty and the issue's list matches it exactly.
- `pipelines ruff check . && pytest` passes locally.

## Where this lives in the repo

**Repo surfaces:** `.github/workflows/weekly-alerts.yml`; `pipelines/pipelines/agents/alerts.py`, `pipelines/data/universe.csv`, `pipelines/data/watchlist_top10.csv`.
