# AlphaStock Triple-Index & Alpha-Screening Model

**Version:** 4.0 · November 14, 2026 (supersedes "AlphaStock Dual-Index & Alpha-Screening Model" v1.0 / methodology v3.0)
**Owner:** Richard Lu (A7 — human approval gate)
**Editable by:** humans, Claude Code, and Obvious agents — this file is the source of truth for the index model; edit it in the same PR as any code or data change it describes.
**Companion documents:** `docs/correlation-model.md` (Correlation & Factor-Relationship Model), `.obvious/skills/*/SKILL.md` (agent contracts), `CLAUDE.md` / `obvious.md` (cross-agent rules).

---

**What changed from v3.0, in one paragraph.** The two-universe model (a 20/60/20 Mixed universe as "AS-100" and a ≥ $10B Large-Cap 100 as "AS-MC100") becomes three equal-weight indices with new terminology: **AS-100** is now the Large-Cap 100 (every name ≥ $10B), and two mixed-cap indices replace the Mixed universe — **AS-MCG100 (Mixed-Cap-Growth)**, 40% $2–10B / 40% $250M–$2B / 20% < $250M, and **AS-MCB100 (Mixed-Cap-Balanced)**, 70% / 25% / 5%. The mixed-cap universes were rebuilt from scratch on 2026-11-13 from You.com discovery research and a Yahoo market-data screen. AS-100 stays disjoint from both mixed-cap indices by cap band; MCG and MCB may share names, because they are two weightings of one sub-$10B opportunity set. All three are backfilled over a 2024-01-02 research frame, analysed by the Correlation & Factor-Relationship Model, and shown in the Explorer, which now renders English or Chinese, never the two mixed.

## 1. The model at a glance

| Layer | What it does | Where it lives |
|---|---|---|
| **Universe construction** | AS-100 from the Large-Cap 100; AS-MCG100 / AS-MCB100 built by rule from researched candidates | `pipelines/data/large_cap_100.csv`; `pipelines/data/mixed_cap_universe.csv` ← `pipelines/pipelines/indices/universe_build.py` |
| **Index calculation** | Equal-weight price-return levels, base 1,000, quarterly rebalance, divisor continuity | `pipelines/pipelines/indices/` (`engine.py`, `compute.py`) |
| **Research backfill** | All three indices from 2024-01-02 with point-in-time entry and sourced corporate events | `pipelines/pipelines/indices/research.py`, `pipelines/data/research/` |
| **Factor relationships** | OLS exposures, rolling correlations, stock leaders, event study | `pipelines/pipelines/correlation/`, `docs/correlation-model.md` |
| **Alpha screening** | Growth + tradability standard; deterministic Alpha Score (unchanged from v3.0) | `pipelines/pipelines/scoring/alpha_score.py` |
| **Explorer** | `/indices`, `/research`, `/factors` (new), signal pages — EN or 中文 | `web/` |
| **Governance** | First-Monday review, same-band replacement, human approval before anything client-facing | §7, `.obvious/skills/qa-attestation/SKILL.md` |

## 2. The three indices

| | **AS-100** — Large-Cap 100 | **AS-MCG100** — Mixed-Cap-Growth 100 | **AS-MCB100** — Mixed-Cap-Balanced 100 |
|---|---|---|---|
| 中文名 | 大盘 100 指数 | 混合市值成长 100 指数 | 混合市值均衡 100 指数 |
| Cap rule | every name ≥ $10B | 40 Mid · 40 Small · 20 Micro | 70 Mid · 25 Small · 5 Micro |
| Universe file | `large_cap_100.csv` | `mixed_cap_universe.csv` (`in_mcg = 1`) | `mixed_cap_universe.csv` (`in_mcb = 1`) |
| Base 1,000 | 2026-11-09 close | 2026-11-13 close | 2026-11-13 close |
| Broad benchmarks (ETF proxies) | SPY, QQQ, RSP | SPY, IWM, IWC | SPY, IJH, IWM |
| Universe label (exclusions / replacements) | `LargeCap100` | `MCG100` | `MCB100` |

Cap bands: **Mid** $2B–< $10B, **Small** $250M–< $2B, **Micro** $25M–< $250M (the $25M floor keeps shells and untradeable stubs out). Equal weight with fixed counts means counts *are* the weights (1.00% per name).

**Invariants.** (1) Neither mixed-cap index may hold an AS-100 constituent: guaranteed by band, and re-checked by `load_compositions()`, which raises on overlap. (2) AS-MCG100 and AS-MCB100 **may** share names: by construction MCB's 70 Mid ⊇ MCG's 40 Mid, MCG's 40 Small ⊇ MCB's 25 Small, and MCG's 20 Micro ⊇ MCB's 5 Micro, so 70 names are shared and 130 are distinct. (3) Every universe file is validated on load (`data/large_cap.py`, `data/mixed_cap.py`): schema, band-vs-cap consistency, exact band counts.

### 2.1 The 2026-11-13 mixed-cap build

Composition of the checked-in universes (11-category taxonomy unchanged from v3.0):

| | AI & Compute | Physical AI | Data-Center REITs & Digital Infra | Cooling & Power | E&C | Nuclear & SMRs | Other Adjacent | Gas Midstream / LNG | Utilities (DC load) | Grid Equipment | Power Producers |
|---|---|---|---|---|---|---|---|---|---|---|---|
| AS-MCG100 | 21 | 21 | 11 | 10 | 10 | 8 | 8 | 5 | 2 | 2 | 2 |
| AS-MCB100 | 22 | 18 | 14 | 7 | 9 | 8 | 4 | 4 | 7 | 4 | 3 |

Growth legs: MCG 80 G1 / 18 G3 / 2 G4; MCB 83 G1 / 11 G3 / 5 G2 / 1 G4. Every qualitative (G2–G4) row carries a one-line note and is first in line for re-verification at the monthly review.

## 3. Construction standard (v4.0)

### 3.1 Growth legs (any one) — unchanged

G1 revenue growth ≥ 10% (v4.0 evidences it with the latest YoY quarterly growth from the screen; a G1 claim the data contradicts disqualifies the name — NWE at 9.99% moved to G2) · G2 named large-load / interconnection exposure · G3 backlog or contracted capacity ≥ 25% of TTM revenue (pre-revenue developers via signed contracts/licensed capacity) · G4 ≥ 20% of revenue from Physical AI.

### 3.2 Tradability (all)

| Test | Mid | Small | Micro |
|---|---|---|---|
| T1 band | $2B–< $10B | $250M–< $2B | $25M–< $250M |
| T2 3-month average daily dollar volume | ≥ $10M | ≥ $3M | ≥ $1M |
| T3 price | ≥ $0.75 | ≥ $0.75 | ≥ $0.75 |

The micro-cap thresholds are a v4.0 proposal (the old $250M micro *floor* became the micro *ceiling*); they are deliberately loose enough to fill 20 names and are the first numbers to revisit if liquidity proves too thin.

### 3.3 Ranking and selection

Within each band: G1 names first, then qualitative legs; ties by dollar volume, then ticker. AS-MCG100 takes the top 40 / 40 / 20 and AS-MCB100 the top 70 / 25 / 5 of the same ranked bands. The rule is code (`universe_build.py`), so the committed CSV is reproducible: a test asserts the builder regenerates it byte-for-byte.

### 3.4 Research behind the build (You.com API)

1. **Discovery** — 43 You.com Search API queries (11 categories × 3 bands, plus 10 micro-cap niche queries) surfaced 160 candidate tickers; combined with the prior Mixed universe, the 2026-11-09 rebalance names, and a seed list, 338 symbols were screened (`outputs/universe_research/discovery.json`, git-ignored).
2. **Screen** — Yahoo quote data (market cap, price, 3-month volume, YoY revenue growth) for the 292 symbols that returned usable quotes, snapshotted to `data/research/universe_screen.json` (as of 2026-11-13); the rest were noise tokens or delisted symbols.
3. **Theme call** — `data/research/theme_map.csv`: the human judgment of theme fit, category and growth leg, one note per name. Names were added or excluded on You.com evidence, e.g. RIOT (910 MW Corsicana campus leased to Anthropic, ~$28B), AMKR (NVIDIA packaging partnership + 10-year TSMC agreement), HNRG ($700M Merom capacity sale), FCEL ($3.6B backlog); **excluded** DBRG (acquired by SoftBank 2026-09-30 — stale quote) and ACA (pending CRH buyout).
4. **Corporate events** — two You.com queries per constituent across all three indices (230 tickers), curated into `data/research/corporate_events.csv` (§5).

## 4. Index calculation — unchanged mechanics

Equal weight 1.00%; price return (ordinary dividends unadjusted); holdings-based engine with divisor continuity; corporate actions detected as raw-vs-adjusted divergence > 2% and neutralised through holdings; quarterly rebalance on the last trading day of Mar/Jun/Sep/Dec (all three indices on the same dates); missing prices are errors, documented in `index_exclusions.csv` (currently empty — §8); deterministic outputs with SHA-256 attestation. Artifacts: `index_levels.csv`, `index_metadata.json` (`methodology_version` "4.0"), `comparison.csv/.json`.

## 5. Research backfill (2024-01-02 → latest close)

`python -m pipelines.indices.research` runs all three indices through the production engine from the first trading day of 2024 (base 1,000), offline, from the checked-in Yahoo frame `data/research/eod_frame.csv.gz`.

- **Point-in-time entry.** A name not trading on the base date enters at the first quarterly rebalance on/after its sourced regular-way listing date (`data/research/listing_dates.csv`). Pre-listing vendor prints never count: GE Vernova when-issued, OKLO's SPAC shell (AltC), SERV's OTC prints before its 2024-04-18 Nasdaq uplisting, KDK's de-SPAC.
- **Corporate events verified.** Every completed split, reverse split, spin-off, special dividend and exchange-ratio merger in `corporate_events.csv` is checked against the frame on its effective date; a > 25% one-day move means an unadjusted action and fails the run. All 22 verify (largest: FCEL 1-for-30, −10.9% that day).
- **Results (2024-01-02 → 2026-11-13).** AS-100 +142.3% (CAGR +36.2%, vol 22.5%, max DD −22.3%); AS-MCG100 +237.7% (+52.9%, 45.5%, −52.5%); AS-MCB100 +233.3% (+52.3%, 41.4%, −48.0%); SPY +59.6%.
- **Read with the caveats.** This is a current-constituent backtest: universes chosen in Nov 2026 with hindsight, and cap bands are today's, so names that grew into a band are backtested inside it. Survivorship and look-ahead bias inflate the mixed-cap numbers most. Internal research view only — never a published index value.

## 6. Factors library (Correlation & Factor-Relationship Model)

Method: `docs/correlation-model.md` (unchanged formulas; v4.0 adds `--mode research` and three-index support). The Explorer's `/factors` page shows the factor catalog (MKT, SIZE, MICRO, BREADTH, UTIL, NUKE, GRID, ROBO), per-index OLS exposures with t-stats and 63/21-day correlations, the full correlation matrix, and the five constituents per index that co-move hardest with each factor. On the research frame, all three indices load mainly on GRID and ROBO (R² 0.88 AS-100, 0.72 MCG, 0.76 MCB); the two mixed-cap indices correlate 0.98 with each other. The fixture is regenerated by `python -m pipelines.correlation.web_fixture` and test-checked for reproducibility.

## 7. Monthly review, replacement, Alpha Score

First-Monday review per index, separately: re-screen against §3 with fresh data; remove failures; replace within the **same cap band** (and same category where possible) with the highest-ranked qualifying candidate; for mixed-cap indices, a removal from a shared name applies to both; AS-100 overlap is invalid. Delistings, completed acquisitions and halts trigger same-day replacement. Pending deals to watch now: AES ($15 cash, GIP/EQT), TXNM ($61.25, Blackstone), BKH + NWE merger (renamed Bright Horizon Energy on completion), ETN Mobility spin. The Alpha Score and weekly alert mechanics are unchanged from v3.0.

## 8. Decisions and open questions

- **PSTG → P (decided 2026-11-14, A7).** Pure Storage became Everpure and moved NYSE ticker PSTG → P on 2026-04-17; the old "no EOD history" exclusion was a stale ticker. AS-100 holds P; `index_exclusions.csv` is empty. Rule: research a ticker change before documenting any exclusion.
- **Index ids (decided 2026-11-14).** AS-100 = Large-Cap, AS-MCG100 = Mixed-Cap-Growth, AS-MCB100 = Mixed-Cap-Balanced; MCG/MCB may overlap.
- **Open — orphaned large caps.** 22 names in the signal-coverage universe (`data/universe.csv`) now sit in no index, including NVDA, AVGO, TSM, VST, CEG, GEV, ETN, PWR, VRT, EQIX and AEP — the old Mixed universe's large-cap tier and the weekly top-10 watchlist. They were kept out of the Large-Cap 100 only by v3.0's disjointness rule, which v4.0 no longer needs at ≥ $10B. Recommended: fold them into AS-100 at the next monthly review (same-category replacement of the weakest qualifiers).
- **Open — coverage universe.** `data/universe.csv` (signal coverage for scans, alerts, company pages) still holds the v1.0 list; aligning it with the 330 index constituents is a separate change.

## 9. Language policy (Explorer)

The Explorer renders **one language per page — English or 简体中文 — never mixed**. The nav carries a language switch (cookie `as_lang`, route `/lang`); `<html lang>` follows the choice. Dictionaries live in `web/lib/messages.ts`; the 中文 dictionary is typed against the English one, so a missing key fails the build. Content authored in both languages (signal summaries, alert briefs, research caveats) renders the matching version; research prose that exists only in English (company theses, invalidation text, corporate-event details) is labelled as such in the 中文 view rather than silently mixed. Agent-side bilingual parity (EN and ZH drafted for every signal and brief) is unchanged.

## 10. How Obvious (and Claude Code) update the model

Everything that defines the model is plain text in this repo, validated by tests, so an Obvious agent can make any change through a normal PR:

| Change | Edit | Then run | Guard |
|---|---|---|---|
| Add / drop / re-categorise a mixed-cap candidate | `pipelines/data/research/theme_map.csv` | `python -m pipelines.indices.universe_build` | builder rules; `test_universe_build.py` parity |
| Refresh market caps / liquidity | re-screen → `data/research/universe_screen.json` | builder, then research + correlation regeneration | `data/mixed_cap.py` band counts |
| Large-cap replacement | `pipelines/data/large_cap_100.csv` or `index_replacements.csv` | `python -m pipelines.indices` | `data/large_cap.py`; overlap check |
| New listing / corporate event | `data/research/listing_dates.csv`, `corporate_events.csv` (source required) | `python -m pipelines.indices.research --web-out ../web/fixtures/research.json` | frame-adjustment check fails loudly |
| Factors page refresh | — | correlation `--mode research` → `correlation.web_fixture` | `test_factors_web_fixture.py` |
| UI copy | `web/lib/messages.ts` (both `en` and `zh`) | `cd web && npm test && npm run build` | type-level key parity; `i18n.test.tsx` |

Research inputs that need network (`--refresh`, You.com discovery via `YDC_API_KEY`, Yahoo screen) are optional steps; every default run is offline and deterministic. Agent contracts (`.obvious/skills/index-market-data/SKILL.md` and its `.claude/agents/` twin) list these commands; keep both twins in sync in the same PR.

## 11. Caveats and gates (carried from v3.0)

Market caps are point-in-time and unaudited (Yahoo, 2026-11-13; stockanalysis.com for AS-100 at 2026-11-09). Qualitative growth legs are re-verified first. Micro-cap liquidity is thin by construction. Index values are computed internally; client-facing publication needs a licensed vendor, written redistribution rights and compliance review. Foreign issuers are included (e.g. PONY, WRD, HSAI, VNET, GDS, IREN, CAMT). Not investment advice.
