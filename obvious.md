# AlphaStock — Agent Guidance

Cross-agent rules for any Obvious agent (or human operator) working in this repository.
Per-agent contracts live in `.obvious/skills/<agent>/SKILL.md` (A1–A7, seven agents).
Control flows one way: data → signals → verification → staged drafts → human approval →
publication. No agent writes directly to anything a client sees.

## The four non-negotiable rules

1. **Fixture-mode default for demos.** Every pipeline and workflow runs against
   checked-in fixtures unless explicitly told otherwise
   (`daily-alpha.yml` / `weekly-alerts.yml` / `index-daily.yml` default their
   `fixture` dispatch input to `true`; `weekly-scan.yml` offers `--mode fixtures`).
   A demo must be deterministic and network-free. Never point a demo at live
   sources or the production store without saying so in the run record.

2. **No auto-send of client-facing content.** There is deliberately no send path
   in this MVP. Drafts are staged — Daily Alpha as a GitHub issue labeled
   `daily-alpha`, weekly alerts as an issue labeled `weekly-alert` — and only a
   human approval (A7) moves anything to publication. Do not add, wire, or
   "temporarily test" an email/broadcast/publish path. The gate is the product.

3. **Tiingo quota pacing with backoff.** Tiingo is observed running near
   ~50 requests/hour. The index pipeline batches requests, caches closes, and
   backs off on throttling. When touching `pipelines/pipelines/indices/`, keep
   request volume incremental (cache-first), never fan out per-ticker bursts,
   and fail over to fixture mode loudly when `TIINGO_API_KEY` is missing
   (`::warning::` + fixture run), as `index-daily.yml` does.

4. **Append-only attestation on every state change.** Signals and attestations
   are append-only at the database level (triggers in `db/migrations/0001`,
   RLS in `0002`). Publishing goes through
   `pipelines.attestation.publish_with_attestation`, which writes signals and
   their SHA-256 batch attestation in one transaction — a failed attestation
   write means nothing publishes. Never mutate history, never bypass the
   attestation writer, never delete rows to "fix" a bad publish.

## Universe invariants (Triple-Index Methodology v4.0)

Source of truth: `docs/design/alphastock-index-model.md` — edit it in the same PR
as any change to the model, its data, or its rules.

- **Three equal-weight 100-stock indices.** AS-100 (Large-Cap 100, every
  constituent ≥ $10B; `pipelines/data/large_cap_100.csv`), AS-MCG100
  (Mixed-Cap-Growth: 40 Mid $2–10B / 40 Small $250M–2B / 20 Micro < $250M) and
  AS-MCB100 (Mixed-Cap-Balanced: 70 / 25 / 5), both from
  `pipelines/data/mixed_cap_universe.csv`.
- **AS-100 never overlaps a mixed-cap index** (hard invariant, re-checked by
  `load_compositions()`); a change that would put a ≥ $10B name in AS-MCG100 or
  AS-MCB100 is invalid and must be rejected. **AS-MCG100 and AS-MCB100 may share
  names** by design (same bands, different weights).
- **Mixed-cap membership is rule-built, not hand-edited.** Change the inputs
  (`data/research/theme_map.csv`, `data/research/universe_screen.json`) and run
  `python -m pipelines.indices.universe_build`; the loader enforces band counts
  and band-vs-cap consistency, and a test asserts the CSV matches the builder.
- **Quarterly rebalance, monthly review.** Reviews replace qualifying removals
  within the same cap band (and category where possible); every row carries a
  sourced rationale.
- **Late listings and corporate events are sourced.** `data/research/listing_dates.csv`
  gates entry; `data/research/corporate_events.csv` records splits, spin-offs,
  ticker changes and pending deals with a source URL each, and the research run
  fails if a recorded split/spin-off is not adjusted in the price frame.
- **PSTG is now P.** The 2026-11-10 "no EOD history" exclusion was a stale
  ticker: Pure Storage became Everpure and moved NYSE ticker PSTG -> P on
  2026-04-17. Methodology decision 2026-11-14 (A7): the AS-100 constituent
  is `P`, the exclusion is retired, and `index_exclusions.csv` is empty. A
  vendor gap on a constituent must be researched for a ticker change before
  it is documented as an exclusion.
- **Missing constituents are documented, never silently dropped** from index
  computation.
- **Client-facing index publication requires written Tiingo authorization plus
  compliance review.** Computing internal levels is fine; publishing them to
  clients is not, until that authorization exists.

## Explorer language policy

The Explorer renders **English or 简体中文 — one language per page, never
mixed** (nav switch → `/lang`, cookie `as_lang`). All UI copy lives in
`web/lib/messages.ts`; the `zh` dictionary is typed against `en`, so add every
new string to both. Content authored in both languages renders the matching
version; English-only research prose is labelled in the 中文 view. Agent-side
bilingual parity (EN and ZH drafted for every signal and brief) is unchanged.

## Development commands (verified)

```bash
# Python pipelines
cd pipelines && pip install -e ".[dev]"
ruff check . && pytest

# Web Explorer
cd web && npm install && npm run lint && npm test && npm run build

# Migrations (ops; not in CI)
psql "$SUPABASE_DB_URL" -f db/migrations/0001_signal_store.sql
psql "$SUPABASE_DB_URL" -f db/migrations/0002_rls.sql
```

CI (`.github/workflows/ci.yml`) runs ruff + pytest for `pipelines/` and
lint + vitest + build + Playwright for `web/` on every PR and push to `main`.
Secrets are env vars only (`SUPABASE_URL`, `SUPABASE_SERVICE_KEY`,
`SUPABASE_DB_URL`, `SUPABASE_ANON_KEY`, `TIINGO_API_KEY`) — never commit values.
