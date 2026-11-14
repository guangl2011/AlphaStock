# AlphaStock

Bilingual (EN/ZH) signal research on U.S.-listed AI-energy and Physical-AI equities.
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

## Research backfill (2024-01-02 → latest close)

`pipelines/pipelines/indices/research.py` runs both universes through the production
equal-weight engine from the first trading day of 2024 (base 1000), with point-in-time
entry for late listings (gated by sourced regular-way dates in
`pipelines/data/research/listing_dates.csv`), category sub-indices, and risk/return
statistics against all ETF benchmark proxies. It is a current-constituent backtest
(survivorship / look-ahead bias by construction) and an internal research view only.

```bash
cd pipelines
# Deterministic, network-free: reads the checked-in frame data/research/eod_frame.csv.gz
python -m pipelines.indices.research --web-out ../web/fixtures/research.json
# Refresh the frame from Yahoo Finance first (network; pip install -e ".[research]")
python -m pipelines.indices.research --refresh --end 2026-11-13 --web-out ../web/fixtures/research.json
```

The Explorer shows it at `/research` (fixture mode).

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
