# AlphaStock (Grid Alpha)

Bilingual (EN/ZH) market-signal platform: Python data pipelines, an append-only
Supabase signal store, and a Next.js Explorer. Six automated agent roles run the
pipelines and drafts; one human role approves everything client-facing.

**Per-agent contracts (source of truth):** `.obvious/skills/<agent>/SKILL.md`
(A1–A7, seven agents). The `.claude/agents/<agent>.md` files are the Claude Code
twins of the same contracts. If the two ever disagree, fix both in the same PR.

Control flows one way: data → signals → verification → staged drafts → human
approval → publication. No agent writes directly to anything a client sees.

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

## Universe invariants (Two-Universe Methodology v3.0)

- **Two disjoint 100-stock universes**: the Mixed universe (AS-100; tiers
  20 large / 60 small / 20 micro) and the Large-Cap 100 (AS-MC100; every
  constituent ≥ $10B market cap). Disjointness is a hard invariant — a
  monthly-review change that would make the universes overlap is invalid and
  must be rejected.
- **Quarterly rebalance, monthly review.** Reviews replace qualifying removals
  within the same category; every row carries a sourced rationale.
- **PSTG stays excluded** for missing EOD history until the methodology
  changes. Do not "helpfully" add it back to coverage lists or universes.
- **Missing constituents are documented, never silently dropped** from index
  computation.
- **Client-facing index publication requires written Tiingo authorization plus
  compliance review.** Computing internal levels is fine; publishing them to
  clients is not, until that authorization exists.

## The A7 gate — human approval (Richard Lu)

QA & Attestation (A7) is a **person, not automation** — see
`.obvious/skills/qa-attestation/SKILL.md`. Claude Code must NEVER bypass,
simulate, or self-approve this gate. There is no agent-side approval: every
client-facing publication (Daily Alpha briefs, weekly alerts, index levels)
happens only after A7's recorded approval decision on the staged artifact.
Claude Code may prepare evidence for A7 (links, reconciliations) but may never
record an approval decision.

## Contract parity

When changing an agent contract, update BOTH twins in the same PR:

- `.obvious/skills/<agent>/SKILL.md` (source of truth), and
- `.claude/agents/<agent>.md` (Claude Code subagent twin),

keeping Mission / Workflow / Gates / Verification in sync. A PR that changes
one twin without the other is incomplete. A7 has no `.claude/agents/` twin by
design — it is the human role.

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
