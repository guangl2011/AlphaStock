# AlphaStock

Bilingual (EN/ZH) signal research on U.S.-listed AI-energy and Physical-AI equities, with three
equal-weight indices — AS-100 (Large-Cap), AS-MCG100 (Mixed-Cap-Growth) and AS-MCB100
(Mixed-Cap-Balanced). Model: `docs/design/alphastock-index-model.md`.
AlphaStock ingests interconnection-queue and data-center-buildout data, scores covered
companies, and publishes verified research with a tamper-evident track record.

## Layout

| Path | What it is |
| --- | --- |
| `pipelines/` | Python package: queue/permit/EDGAR ingestion, signal detection, Alpha Score, attestation |
| `web/` | Next.js (App Router) read-only Explorer |
| `.github/workflows/` | CI, weekly scan, daily brief, weekly alert automation |
| `db/` | Database migrations (added as the store lands) |
| `docs/` | Spec, ADRs, runbooks |

## Development

```bash
# Python pipelines
cd pipelines
pip install -e ".[dev]"
ruff check . && pytest

# Web Explorer
cd web
npm install
npm run lint && npm test && npm run build
```

CI runs lint and tests for both stacks on every PR and push to `main`.

## Research backfill (2020-01-02 → latest close)

`pipelines/pipelines/indices/research.py` runs all three indices through the production
equal-weight engine from the first trading day of 2020 (base 1000), with point-in-time
entry for late listings (gated by sourced regular-way dates in
`pipelines/data/research/listing_dates.csv`), category sub-indices, and risk/return
statistics against all ETF benchmark proxies. It is a current-constituent backtest
(survivorship / look-ahead bias by construction) and an internal research view only.
It also precomputes selectable periods (5Y / 3Y / 1Y / YTD and each calendar year, each
measured from the prior close); the Explorer's `/research?period=…` switches the KPIs,
chart, tables and corporate events to the chosen period.

```bash
cd pipelines
# Deterministic, network-free: reads the checked-in frame data/research/eod_frame.csv.gz
python -m pipelines.indices.research --web-out ../web/fixtures/research.json
# Refresh the frame from Yahoo Finance first (network; pip install -e ".[research]")
python -m pipelines.indices.research --refresh --end 2026-11-13 --web-out ../web/fixtures/research.json
```

Corporate events (splits, spin-offs, ticker changes, de-SPAC listings, pending deals) are
researched per constituent with the You.com Search API, curated with sources into
`pipelines/data/research/corporate_events.csv`, and every split / spin-off is checked to be
adjusted in the price frame on its effective date (the run fails otherwise):

```bash
# Network; raw responses cache under outputs/ydc_cache/ (git-ignored). Key via env only.
YDC_API_KEY=... python -m pipelines.indices.corp_events --report outputs/corp_event_candidates.json
```

The Explorer shows it at `/research`, and the Correlation & Factor-Relationship Model's
research-mode output at `/factors` (fixture mode). The mixed-cap universes are rule-built:

```bash
python -m pipelines.indices.universe_build   # data/research/theme_map.csv + universe_screen.json -> data/mixed_cap_universe.csv
```

The Explorer renders English or 中文 — one language per page (switch in the nav).

## Database (Supabase Postgres)

The signal store is Supabase Postgres. Migrations live in `db/migrations/` and apply
in filename order:

```bash
psql "$SUPABASE_DB_URL" -f db/migrations/0001_signal_store.sql
psql "$SUPABASE_DB_URL" -f db/migrations/0002_rls.sql
```

`0001` creates the five tables (`signals`, `companies`, `attestations`, `source_health`,
`alert_history`) and makes `signals` and `attestations` append-only by trigger.
`0002` enables row-level security on every table with a single `FOR SELECT TO anon`
policy each, so the anon role is read-only; writes go through the service role only.

### Environment variables

All wiring is via env vars — never commit secrets or connection strings:

| Variable | Used by | Purpose |
| --- | --- | --- |
| `SUPABASE_DB_URL` | migrations, ops | Postgres connection string for `psql` / psycopg |
| `SUPABASE_URL` | pipelines | Supabase project URL (REST) |
| `SUPABASE_SERVICE_KEY` | pipelines | Service-role key — bypasses RLS, server-side only |
| `SUPABASE_ANON_KEY` | web/ | Anon key — read-only under RLS, safe for the Explorer |

Publishing uses hash-before-serve: `pipelines.attestation.publish_with_attestation`
writes signals and their SHA-256 batch attestation in one transaction, so a failed
attestation write means nothing publishes. Tests run against in-memory fakes and
static migration checks — no live Supabase dependency in CI.

Status: pre-MVP scaffold. See the AlphaStock MVP Build Specification for the roadmap.
