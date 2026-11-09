# AlphaStock

Bilingual (EN/ZH) signal research on U.S.-listed AI-energy and Physical-AI equities.
AlphaStock ingests interconnection-queue and data-center-buildout data, scores covered
companies, and publishes verified research with a tamper-evident track record.

## Layout

| Path | What it is |
| --- | --- |
| `pipelines/` | Python package: queue/permit/EDGAR ingestion, signal detection, Alpha Score, attestation |
| `web/` | Next.js (App Router) read-only Explorer |
| `.github/workflows/` | CI, weekly scan, daily brief automation |
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

Status: pre-MVP scaffold. See the AlphaStock MVP Build Specification for the roadmap.
