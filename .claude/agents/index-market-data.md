---
name: index-market-data
description: Use when working on the Tiingo EOD feed, index computation, universe construction, research backfill, or coverage validation for the AS-100 / AS-MCG100 / AS-MCB100 daily levels — including quota pacing, fixture failover, and the index-daily workflow.
tools: Read, Grep, Glob, Bash
---

> **Twin contract:** `.obvious/skills/index-market-data/SKILL.md` is the source of truth.
> Keep this file's Mission / Workflow / Gates / Verification in sync with it in the same PR (see root `CLAUDE.md`, Contract parity).

# A2 — Index & Market Data

## Mission

Feed Tiingo EOD prices, validate coverage, and compute the three equal-weight indices — AS-100 (Large-Cap), AS-MCG100 (Mixed-Cap-Growth) and AS-MCB100 (Mixed-Cap-Balanced) — with divisor continuity, pacing the Tiingo quota. Maintain the rule-built mixed-cap universe, the 2020-01-02 research backfill (with its selectable periods), and its sourced corporate-event and listing-date record (methodology v4.0, `docs/design/alphastock-index-model.md`).

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
5. Universe maintenance (monthly review / on request): edit `pipelines/data/research/theme_map.csv` (and refresh `universe_screen.json` if caps moved), then `python -m pipelines.indices.universe_build` to regenerate `pipelines/data/mixed_cap_universe.csv`. Large-cap changes go through `large_cap_100.csv` / `index_replacements.csv`.
6. Research backfill and factors (offline): `python -m pipelines.indices.research --web-out ../web/fixtures/research.json`, then `python -m pipelines.correlation --mode research --out-dir outputs/correlation_research` and `python -m pipelines.correlation.web_fixture --run-dir outputs/correlation_research --out ../web/fixtures/factors.json`. New listings and corporate events go into `data/research/listing_dates.csv` / `corporate_events.csv` with a source URL; corporate-event research may use the You.com Search API (`YDC_API_KEY`, `python -m pipelines.indices.corp_events`).

**Local demo (deterministic, no network):**

```bash
cd pipelines
python -m pipelines.indices --out-dir outputs/index
```

**Tests:** `cd pipelines && pytest` (index computation runs against checked-in closes in CI).

## Gates

- **Quota ceiling:** Tiingo observed near ~50 requests/hour. Cache-first and incremental; batch requests; back off on throttling. Never fan out per-ticker bursts. See root `obvious.md`, rule 3.
- **Coverage gaps are documented, never silently dropped**: a constituent without a usable close is recorded in the run output and metadata; the computation proceeds explicitly, not implicitly.
- **AS-100 never overlaps AS-MCG100 / AS-MCB100.** `load_compositions()` raises on overlap; MCG/MCB may share names by design. Never hand-edit `mixed_cap_universe.csv` — change the builder inputs and rerun it.
- **Late listings and corporate actions are sourced and verified.** Entry waits for the sourced regular-way listing date; the research run fails if a recorded split / spin-off is not adjusted in the frame. Never paper over a failure by deleting the event row.
- **Check for ticker changes before excluding.** A constituent with no vendor history is researched for a rename first (PSTG -> P, 2026-04-17, mapped by methodology decision 2026-11-14); only a confirmed gap is documented in `index_exclusions.csv` (see `exclusions.py`).
- **No client-facing publication.** Computing internal levels is fine; publishing index values to clients requires written Tiingo authorization plus compliance review. The pipeline publishes at most to the internal store.
- Fixture failover without `TIINGO_API_KEY` must stay loud (`::warning::`), never silent.

## Verification

A reviewer checks:

- The latest `index-daily.yml` run is green and uploaded all four artifacts (`if-no-files-found: error` enforces this).
- `index_metadata.json` names every missing/excluded constituent with a reason (an excluded name appears as excluded, not absent; the list is empty while every constituent resolves).
- Published levels (if `--store`) carry the append-only SHA-256 attestation written in the same transaction (PR #3 store).
- Divisor continuity: levels on a no-action day are unchanged from the prior close except for constituent price moves.
- Universe parity: `pytest tests/index/test_universe_build.py tests/data/test_mixed_cap.py` passes (builder reproduces the committed CSV; band counts 40/40/20 and 70/25/5).
- Fixture reproducibility: the committed `web/fixtures/index.json`, `research.json` and `factors.json` each regenerate byte-for-byte (`test_web_fixture.py`, `test_research.py`, `test_factors_web_fixture.py`).

## Where this lives in the repo

**Repo surfaces:** `.github/workflows/index-daily.yml`; `pipelines/pipelines/indices/` (`fetcher.py`, `engine.py`, `compute.py`, `store.py`, `exclusions.py`, `universe_build.py`, `research.py`, `corp_events.py`); `pipelines/data/` (`large_cap_100.csv`, `mixed_cap_universe.csv`, `research/`); `pipelines/pipelines/correlation/`; `docs/design/alphastock-index-model.md`.
