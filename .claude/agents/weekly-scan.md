---
name: weekly-scan
description: Use when working on the Monday full-universe scan and aggregate weekly report — the weekly-scan workflow, source-health gating of the report, or the Explorer /weekly data contract.
tools: Read, Grep, Glob, Bash
---

> **Twin contract:** `.obvious/skills/weekly-scan/SKILL.md` is the source of truth.
> Keep this file's Mission / Workflow / Gates / Verification in sync with it in the same PR (see root `CLAUDE.md`, Contract parity).

# A5 — Weekly Scan

## Mission

Run the Monday scan of the full universe and produce the aggregate weekly report behind the source-health gate.

## Workflow

**Trigger:** `.github/workflows/weekly-scan.yml` — schedule `0 6 * * 1` (Monday 06:00 UTC) or `workflow_dispatch` with `mode: live|fixtures`, optional `as_of`, and `publish` (default `true`). The same run that serves A1's intake legs produces this agent's report; see `.obvious/skills/intake-supervisor/SKILL.md` for the ingest detail.

**Steps:**

1. Run the scan (`pipelines/pipelines/scan.py`) across all 7 ISO queues + the EDGAR FTS leg behind the health gate (`--all-isos --edgar`).
2. Aggregate the week's signals with `pipelines/pipelines/report/weekly.py` + `report/aggregates.py` (PR #9) into the weekly report: a markdown page plus queryable JSON for the Explorer's `/weekly` route.
3. Commit the rotated snapshot and rendered report back to `main` (the workflow holds `contents: write`).

**Local demo (deterministic, pinned as-of, no network):**

```bash
cd pipelines
python -m pipelines.scan --mode fixtures --all-isos --edgar \
  --as-of 2026-11-09T06:00:00Z \
  --signals-out /tmp/weekly/signals.json \
  --health-out /tmp/weekly/health.json
```

**Tests:** `cd pipelines && pytest` (report aggregation is unit-tested against fixture scans).

## Gates

- **Source-health failure halts the scan**: `assert_scan_healthy` raises `SourceHealthError` before anything publishes — degraded data never becomes a report. A red workflow is correct; never widen the age budget (`max_age_days=7`) or downgrade a failure to make a week render.
- A quiet week with healthy sources is a legitimate **empty scan** — an honest empty report beats a padded one.
- The county-permitting leg's missing live collector is a recorded plan decision in the workflow header — keep it visible; do not quietly fold counties into "no data."
- Fixture mode (`--mode fixtures` with `--as-of`) never touches the network or the store.

## Verification

A reviewer checks:

- The Monday run of `weekly-scan.yml` is green and committed both `data/snapshots/queue_snapshot.json` and the weekly report to `main`.
- The report's as-of instant and signal counts reconcile with the `signals` rows published that run (append-only store, PR #3).
- The Explorer `/weekly` route renders the committed report JSON.
- `pipelines ruff check . && pytest` passes locally.

## Where this lives in the repo

**Repo surfaces:** `.github/workflows/weekly-scan.yml`; `pipelines/pipelines/scan.py`, `pipelines/pipelines/report/weekly.py`, `pipelines/pipelines/report/aggregates.py`.
