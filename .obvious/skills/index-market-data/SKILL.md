---
name: index-market-data
description: Use when working on the Tiingo EOD feed, index computation, or coverage validation for the AS-100 / AS-MC100 daily levels — including quota pacing, fixture failover, and the index-daily workflow.
---

# A2 — Index & Market Data

## Mission

Feed Tiingo EOD prices, validate coverage, and compute AS-100 / AS-MC100 daily levels with divisor continuity, pacing the Tiingo quota.

## Workflow

**Trigger:** `.github/workflows/index-daily.yml` — schedule `30 21 * * 1-5` (post-U.S.-close, 21:30 UTC) or `workflow_dispatch` with `fixture` (default `true`) and `publish` (default `false`).

**Steps:**

1. Run the index pipeline (`pipelines/pipelines/indices/`, PR #15):
   - Fixture mode (default): `python -m pipelines.indices --out-dir outputs/index` — checked-in closes, no Tiingo call, no secrets.
   - Live mode: `python -m pipelines.indices --mode live --out-dir outputs/index` with `TIINGO_API_KEY` set. Missing secret → loud `::warning::` and fixture failover, never a silent wrong-level run.
   - Publish levels to Supabase only with `--store` and `SUPABASE_DB_URL` set.
2. Fetch Tiingo EOD closes cache-first (`fetcher.py`); compute equal-weight levels from base 1,000 with divisor continuity and corporate-action adjustments (`engine.py`, `compute.py`).
3. Validate coverage (`store.py`, `exclusions.py`): every constituent must resolve with adequate history; gaps are reported, not papered over.
4. Emit artifacts: `outputs/index/index_levels.csv`, `index_metadata.json`, `comparison.csv`, `comparison.json` (benchmark proxies per `benchmarks.py`, date handling in `dates.py`); regenerate the Explorer fixtures with `web_fixture.py` / `replacements.py` when the `/indices` view needs fresh fixture data (PR #16).

**Local demo (deterministic, no network):**

```bash
cd pipelines
python -m pipelines.indices --out-dir outputs/index
```

**Tests:** `cd pipelines && pytest` (index computation runs against checked-in closes in CI).

## Gates

- **Quota ceiling:** Tiingo observed near ~50 requests/hour. Cache-first and incremental; batch requests; back off on throttling. Never fan out per-ticker bursts. See root `obvious.md`, rule 3.
- **Coverage gaps are documented, never silently dropped**: a constituent without a usable close is recorded in the run output and metadata; the computation proceeds explicitly, not implicitly.
- **PSTG stays excluded** for missing EOD history (see `exclusions.py`) until the methodology changes.
- **No client-facing publication.** Computing internal levels is fine; publishing index values to clients requires written Tiingo authorization plus compliance review. The pipeline publishes at most to the internal store.
- Fixture failover without `TIINGO_API_KEY` must stay loud (`::warning::`), never silent.

## Verification

A reviewer checks:

- The latest `index-daily.yml` run is green and uploaded all four artifacts (`if-no-files-found: error` enforces this).
- `index_metadata.json` names every missing/excluded constituent with a reason (PSTG appears as excluded, not absent).
- Published levels (if `--store`) carry the append-only SHA-256 attestation written in the same transaction (PR #3 store).
- Divisor continuity: levels on a no-action day are unchanged from the prior close except for constituent price moves.
