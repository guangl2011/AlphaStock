---
name: intake-supervisor
description: Use when working on the data-intake pipelines — ISO interconnection queues, EDGAR filings, or county permitting signals — including the weekly-scan source-health gate and anything that writes candidate signals to the store.
tools: Read, Grep, Glob, Bash
---

> **Twin contract:** `.obvious/skills/intake-supervisor/SKILL.md` is the source of truth.
> Keep this file's Mission / Workflow / Gates / Verification in sync with it in the same PR (see root `CLAUDE.md`, Contract parity).

# A1 — Intake Supervisor

## Mission

Run and triage the three data pipelines: ISO interconnection queues (7 ISOs), EDGAR filings, and county permitting signals.

## Workflow

**Trigger:** `.github/workflows/weekly-scan.yml` — schedule `0 6 * * 1` (Monday 06:00 UTC) or manual `workflow_dispatch` with `mode: live|fixtures`.

**Steps:**

1. Run the scan runner (`pipelines/pipelines/scan.py`), which fans out to:
   - **Queues (V2 leg, PR #6):** `pipelines/pipelines/ingest/queues/` — adapters per ISO (`pjm.py`, `ercot.py`, ...), source definitions in `sources.py`, per-ISO column mappings in `mappings/{isone,ercot,miso,pjm,spp,caiso,nyiso}.yaml`.
   - **EDGAR (V4 leg 1, PR #5):** `pipelines/pipelines/ingest/edgar.py` — full-text search sweep against the coverage universe.
   - **County permitting (PR #5):** `pipelines/pipelines/ingest/counties.py` against `data/county_watchlist.csv`. No live collector yet — collection is operator-supplied JSON; the scheduled run covering queues + EDGAR is a recorded plan decision, not a silent source failure.
2. Evaluate source health (`pipelines/pipelines/ingest/source_health.py`): `evaluate_freshness` marks a source `ok` within `max_age_days=7`, `stale` beyond it, `failed` on fetch error.
3. On green health, diff against the previous snapshot (`--snapshot data/snapshots/queue_snapshot.json`) to detect transitions.
4. Publish new signals + health rows to the store (`--publish`, needs `SUPABASE_DB_URL`) and commit the rotated snapshot + weekly report back to `main`.

**Local demo (deterministic, no network):**

```bash
cd pipelines
python -m pipelines.scan --mode fixtures --all-isos --edgar \
  --as-of 2026-11-09T06:00:00Z \
  --signals-out /tmp/signals.json --health-out /tmp/health.json
```

**Tests:** `cd pipelines && pytest` (queue diff, health, and ingest legs are unit-tested against fixtures — no network in CI).

## Gates

- **Source-health gate is fatal and runs before anything publishes**: `assert_scan_healthy` raises `SourceHealthError` if any source is anything other than `ok` — **stale or failed both turn the workflow red** (stale is only a warning on non-scan publish paths, via the looser `assert_healthy`). Never skip the gate, never widen the freshness budget, never silence the failure to "get a week out."
- A quiet week with healthy sources is a legitimate **empty scan** — publish the honest empty result; do not fabricate signals.
- Fixture mode never touches the network or the store.
- All writes go through the append-only attestation path (see root `obvious.md`, rule 4).

## Verification

A reviewer checks:

- The Monday run of `weekly-scan.yml` is green (or red **with** a named failed source in `source_health` — never a silent pass).
- `data/snapshots/queue_snapshot.json` rotated on `main` with the run's commit.
- New signals exist as `signals` rows with a SHA-256 batch attestation in `attestations` (query the store; PR #3 schema).
- `pipelines ruff check . && pytest` passes locally.

## Where this lives in the repo

**Repo surfaces:** `.github/workflows/weekly-scan.yml`; `pipelines/pipelines/scan.py`, `pipelines/pipelines/ingest/` (queues, `edgar.py`, `counties.py`, `source_health.py`).
