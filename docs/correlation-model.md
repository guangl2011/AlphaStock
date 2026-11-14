# Correlation & Factor-Relationship Model

Method document for `pipelines/pipelines/correlation/` (merged in PR #19). Every
formula, window, threshold, and output schema below is stated to match the
implemented code exactly; the worked example uses numbers from an actual
fixture run of `python -m pipelines.correlation`. Updated for methodology v4.0
(three indices: AS-100 Large-Cap, AS-MCG100 Mixed-Cap-Growth, AS-MCB100
Mixed-Cap-Balanced) and the research mode that feeds the Explorer's Factors
library (`/factors`).

## Purpose

AlphaStock publishes three equal-weight indices and a deterministic Alpha Score,
but scores and levels alone do not quantify relationships. This model answers
three questions with plain-float statistics:

1. How much of each index's daily move is market beta versus small-cap,
   micro-cap, breadth, and thematic exposure?
2. How does each constituent's price co-move with the market and the thematic
   comparators over a trailing window?
3. Do the platform's V1–V4 signal events relate to price at all — measured as
   the beta-adjusted abnormal return around each event date?

All computation runs on the platform's determinism contract: pure Python
standard library, no numpy/pandas/scipy, closed-form float math, sorted
iteration everywhere. Two runs over the same inputs produce byte-identical
outputs, and every CSV is SHA-256 attested in `correlation_metadata.json`.

Run it with `python -m pipelines.correlation` (fixture mode by default, no
network or secrets; `--mode live` reads Tiingo EOD through the same client and
cache the index pipeline uses, paced and quota-aware; `--mode research` reads
the checked-in 2024-01-02 research frame and the three backfilled index
series, offline). The model is index-agnostic: it fits every `index_id` present
in the levels file.

### Research mode and the Factors library

```bash
cd pipelines
python -m pipelines.indices.research                       # writes outputs/research/
python -m pipelines.correlation --mode research --out-dir outputs/correlation_research
python -m pipelines.correlation.web_fixture --run-dir outputs/correlation_research \
    --out ../web/fixtures/factors.json
```

Research mode prices every constituent of the three indices from the research
frame (Yahoo EOD, split- and spin-off-adjusted, events verified per
`data/research/corporate_events.csv`). The checked-in signal events are
synthetic fixture rows, so research mode skips the event study rather than mix
fixture events with real prices. The web fixture is a pure selection of the run's
outputs (catalog, per-index exposures, the full correlation matrix, and the five
constituents with the highest trailing 63-day correlation per factor); a test
asserts the committed `web/fixtures/factors.json` regenerates byte-for-byte.

## Factor definitions

Factors are daily-return spreads of ETF proxies already in
`pipelines.indices.benchmarks.PROXY_MAP`. Licensed index values are never
fetched or republished. Factors are daily *returns*, not levels: spreads are
same-day differences of returns, so the series are stationary and comparable
across proxies.

| Factor | Construction | Meaning | In index OLS? |
|---|---|---|---|
| `MKT` | SPY daily return | Market beta | yes |
| `SIZE` | IWM daily return − SPY daily return | Small-cap exposure | correlation only |
| `MICRO` | IWC daily return − SPY daily return | Micro-cap exposure | correlation only |
| `BREADTH` | RSP daily return − SPY daily return | Equal-weight vs cap-weight breadth | yes |
| `UTIL` | XLU daily return | Utilities comparator | yes |
| `NUKE` | URA daily return | Uranium comparator | yes |
| `GRID` | GRID daily return | Clean-energy / grid comparator | yes |
| `ROBO` | ROBO daily return | Robotics & AI comparator | yes |

The six factors marked "yes" form `REGRESSION_FACTORS` and enter the index
exposure regression; `SIZE` and `MICRO` get correlations only, because both
overlap `MKT` heavily. The stock-level summary covers five factors —
`GRID`, `MKT`, `NUKE`, `ROBO`, `UTIL` (`STOCK_FACTORS`).

Returns are close-to-close: `r_t = close_t / close_{t-1} − 1`. A non-positive
close anywhere in a proxy's history raises `FactorError` rather than silently
corrupting ratios. A spread factor is defined only on dates where both legs
have a return; dates where exactly one leg is missing are dropped and reported
per factor in `alignment_gaps` in the run metadata. A proxy with no records at
all fails loudly.

## Formulas

All statistics are computed by `pipelines/correlation/stats.py` in plain
floats. Undefined results are `None` and serialize as an empty CSV field —
never a silently coerced `0.0`.

### Pearson correlation

For aligned pairs `(x_i, y_i)`, i = 1..n:

```
r = Σ(dx·dy) / sqrt(Σdx² · Σdy²)      where dx = x_i − x̄, dy = y_i − ȳ
```

`None` when n < 2 or either series has zero variance.

### Closed-form OLS

The index exposure regression fits, per index:

```
r_index,t = α + Σ_k β_k · f_k,t + ε_t
```

over `REGRESSION_FACTORS` (six factors) plus an intercept. The normal
equations (X'X, X'y, accumulated with `math.fsum`) are solved by
Gauss–Jordan elimination with partial pivoting.

- **Collinearity drop.** During elimination, a pivot whose magnitude is at
  most 10⁻¹⁰ of the normal matrix's largest absolute entry marks a collinear
  column. That factor is dropped and the solve retried; the dropped names are
  recorded in `dropped_collinear` on every affected row and in the output —
  never silently merged. The intercept column is never dropped.
- **Standard errors and t-stats.** With n observations and k active factors,
  dof = n − k − 1; σ² = RSS/dof; SE_j = sqrt(σ² · (X'X)⁻¹_{jj}) (variance
  clamped at zero against numerical noise); t_j = β_j / SE_j. A standard
  error of exactly zero (perfect fit) gives a `None` t-stat.
- **R²** = 1 − RSS/TSS, `None` when TSS is zero (constant y).
- A fit needs at least two observations and, after any drops, enough dof;
  otherwise the model raises loudly.

The stock-level beta is the same OLS restricted to one factor plus intercept:
`r_stock = α + β·f + ε` over the trailing window's aligned returns.

### Beta-adjusted abnormal return (signal-event study)

For a signal event on trading day `t` (the signal's `published_at` date):

1. **Estimation.** β is the single-factor OLS slope of the stock's returns on
   `MKT` returns over the 63 returns immediately before the event —
   `r_{t−63} .. r_{t−1}`. No data on or after `t` enters the estimate.
2. **Event window.** The event measures the compounded close-to-close return
   from the close at `t−1` to the close at `t+3`, which is the compounded sum
   of returns `r_t .. r_{t+3}` (four returns, `EVENT_AFTER = 3`):

   ```
   stock_cum  = Π(1 + r_stock,i)  − 1     over i = t .. t+3
   market_cum = Π(1 + r_MKT,i)    − 1     over the same days
   ```

3. **Abnormal return.**

   ```
   AR = stock_cum − β · market_cum
   ```

Per-event rows carry the β, both compounded returns, and the AR; aggregates
report the mean raw AR by direction and by category.

Missing inputs fail loudly: an event date absent from the market's trading
days, an event window past the data end, insufficient estimation history, or a
covered ticker missing any return in the estimation or event window all raise
`ModelError` — never a silent skip. The same applies to missing prices
throughout the module, matching the index pipeline's contract.

## Window rules

All windows are trailing only — a value computed from data up to day `t` is
unchanged when day `t+1` data is appended. There is no centered or
future-looking data anywhere.

| Quantity | Window | Rule |
|---|---|---|
| Index factor correlation | full | all aligned return days of the series |
| Index factor correlation | 63d / 21d | final 63 / 21 aligned days; `None` (empty) if fewer than 63 / 21 exist |
| Index OLS regression | full sample | all dates where index and all six regression factors align |
| Stock beta and correlation | 63d | last 63 aligned returns (`WINDOW_LONG = 63`) |
| Rolling correlation (`rolling_pearson`) | per position | trailing `window` values; positions with fewer than `window` observations read as `None` — partial windows are never mixed in |
| Event-study beta estimation | 63 returns before `t` | `r_{t−63} .. r_{t−1}` (`ESTIMATION_WINDOW = 63`) |
| Event window | `t−1` close to `t+3` close | returns `r_t .. r_{t+3}` (`EVENT_AFTER = 3`) |

Stock-level details: the trailing beta needs at least 3 aligned observations
and more than one distinct factor value, otherwise it is `None`; the trailing
correlation needs the full 63-day window, otherwise `None`.

## Outputs

`python -m pipelines.correlation` writes four files to `--out-dir`
(default `outputs/correlation`). All floats are formatted to fixed six
decimals; `None` serializes as empty. Line endings are `\n`, iteration is
sorted, and the metadata JSON is emitted with `sort_keys=True` — two runs over
the same inputs are byte-identical.

### factor_exposures.csv

One row per index × factor (all three indices × all eight factors, sorted). One OLS
fit per index; the same collinearity report and R² apply to every row of that
index.

| Column | Meaning |
|---|---|
| `index_id` | `AS-100`, `AS-MCG100` or `AS-MCB100` |
| `factor` | factor name |
| `beta`, `std_error`, `t_stat` | OLS terms — empty for `SIZE`/`MICRO` (correlation-only factors) |
| `r_squared` | regression R² (per index) |
| `corr_full`, `corr_63d`, `corr_21d` | Pearson vs the factor, full sample and trailing windows |
| `dropped_collinear` | `;`-joined names of factors dropped as collinear (empty when none) |
| `regression_days` | aligned days the index's OLS fit used |

### stock_factor_summary.csv

One block per ticker (sorted): five factor rows, then one row per direction
with events.

| Column | Meaning |
|---|---|
| `ticker`, `factor` | constituent and factor (`GRID`, `MKT`, `NUKE`, `ROBO`, `UTIL`), or `event_<direction>` |
| `beta_63d`, `corr_63d` | trailing 63-day single-factor OLS beta and Pearson correlation (factor rows) |
| `mean_abnormal_return` | mean AR of that ticker's events in this direction (`event_*` rows) |
| `abnormal_return_events` | event count behind that mean |

### correlation_matrices.csv

Pairwise correlations among the three indices and all eight factors (55 pairs ×
three windows): columns `series_a`, `series_b`, `window` (`full`, `63d`,
`21d`), `correlation`. The windowed value is the final trailing-window value —
the last entry of the rolling series, which by construction uses only data up
to the series end.

### correlation_metadata.json

Attestation and run record: `methodology_version` (`1.0`), `source`
(`fixture` or `live-tiingo`), factor definitions and regression/correlation
factor lists, window rules, `alignment_gaps` per spread factor, event
aggregates by direction and category, per-event rows (signal id, ticker,
category, direction, event date, β, AR), the benchmark proxy-mapping note, and
the three SHA-256 hashes — `sha256_factor_exposures_csv`,
`sha256_stock_factor_summary_csv`, `sha256_correlation_matrices_csv` — each
taken over the exact CSV bytes written.

## Worked fixture example

The checked-in fixtures (`pipelines/tests/fixtures/correlation/`) carry closes
for 2026-06-01 through 2026-11-13 (~120 trading days), synthetic index levels for
all three indices, and five signal events. A fixture run of
`python -m pipelines.correlation` produced the numbers below (verbatim from
that run's outputs).

**Index exposure (AS-100).** The OLS fit used 119 aligned return days and
returned, with no factors dropped as collinear:

| Factor | β | SE | t | corr_full | corr_63d | corr_21d |
|---|---|---|---|---|---|---|
| `MKT` | 0.827700 | 0.024185 | 34.224403 | 0.952658 | 0.956366 | 0.956016 |
| `GRID` | 0.188110 | 0.017444 | 10.783332 | 0.178605 | 0.109199 | 0.074894 |
| `BREADTH` | 0.018648 | 0.018814 | 0.991134 | −0.679041 | −0.704049 | −0.686545 |
| `UTIL` | 0.028385 | 0.018290 | 1.551932 | 0.088354 | 0.088033 | −0.046928 |
| `NUKE` | −0.007024 | 0.019540 | −0.359460 | −0.107699 | −0.073296 | 0.497911 |
| `ROBO` | 0.003656 | 0.018821 | 0.194271 | −0.130817 | −0.073704 | −0.240300 |

R² = 0.956744. `SIZE` and `MICRO` rows carry empty β/SE/t (correlation-only)
with corr_full −0.001090 and −0.626149. On AS-MCG100 (Mixed-Cap-Growth, the
index with the deepest small- and micro-cap tilt) the market beta is higher,
1.313642 (t = 44.595023, R² = 0.974943); AS-MCB100 (Mixed-Cap-Balanced) sits
between, 1.142866 (t = 41.169552, R² = 0.970083). AS-100 correlates 0.939520
with AS-MCG100 and 0.938718 with AS-MCB100 full-sample; the two mixed-cap
indices correlate 0.981230.

> Correction (v4.0): earlier versions of this example called AS-MC100 "the
> micro-cap index". Under methodology v3.0 AS-MC100 was the Large-Cap 100 (every
> name ≥ $10B); the synthetic small-cap-loaded series that example described is
> now labelled AS-MCG100, and the Large-Cap index is AS-100.

**One event, hand-checked.** Signal `SIG-2026-0915-A` (category
`V2_interconnection`, direction positive, published 2026-09-15 20:00 UTC,
tickers AAOI and AMSC). For AAOI, the 63 returns before the event give β =
0.738665. Over `r_t .. r_{t+3}` the stock compounded −0.5217% while the market
(SPY) compounded −1.0541%:

```
AR = −0.005217 − 0.738665 × (−0.010541) = +0.002569
```

The stock outperformed its beta-adjusted market expectation by +0.2569% over
the event window. Across the fixture's five signals (eight stock-level
events), mean abnormal returns are +0.4239% for the five positive-direction
events, −0.4144% for the single negative event, and +0.4279% for the single
neutral event; by category, `V1_news` −0.4144% (1 event), `V2_interconnection`
+0.4137% (3), `V3_queue` +0.4279% (1), `V4_permit` +0.4390% (2). The fixture's
event SHA-256 attestations for this run: factor_exposures.csv
`0b71ab4…791c46`, stock_factor_summary.csv `a665471…b47c47`,
correlation_matrices.csv `6254ce0…180b27`.

## Caveats

- **Short price history.** The fixture spans ~120 trading days; several
  constituents list more recently still. Full-sample correlations on short
  histories are noisy, and 63-day windows consume half of it. Treat levels of
  β and R² as provisional until the live history accumulates.
- **ETF proxies, not licensed indices.** Factors are built solely from ETF
  proxies in `PROXY_MAP` (SPY, IWM, IWC, QQQ, RSP, XLU, URA, GRID, ROBO). An
  ETF tracks — does not equal — the licensed index, and each carries its own
  fee, tracking error, and composition drift. Republishing derived index
  values externally would additionally require vendor authorization; outputs
  stay inside the platform.
- **Correlation is not causation.** A high β or correlation says nothing about
  why two series move together. The event study's small fixture sample (eight
  events) especially cannot distinguish signal value from market drift; its
  aggregates are a sanity check, not evidence of alpha.
- **Determinism over numerical sophistication.** Pure-stdlib float math with
  `math.fsum` and a 10⁻¹⁰ relative pivot tolerance is deterministic and
  reproducible but not immune to ill-conditioning. The collinearity drop keeps
  the closed-form solve honest; near-collinear (not exactly collinear) factor
  sets can still inflate standard errors. Byte-identical reruns are a feature
  of fixed float formatting and sorted iteration, not of arbitrary precision.
- **Empty means undefined.** `None` statistics serialize as empty CSV fields.
  Downstream consumers must treat empty as "not estimable", never as zero —
  reading it as 0 would, for example, treat an unestimable beta as market
  neutrality, which is a strong and wrong claim.
