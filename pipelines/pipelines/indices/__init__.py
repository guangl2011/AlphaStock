"""Tiingo-fed AS-100 / AS-MCG100 / AS-MCB100 daily index computation pipeline.

Methodology v4.0 (docs/design/alphastock-index-model.md): three equal-weight
price-return indices, base 1000. AS-100 over the Large-Cap 100 (every name
>= $10B), based at the 2026-11-09 close; AS-MCG100 (Mixed-Cap-Growth, 40 Mid /
40 Small / 20 Micro) and AS-MCB100 (Mixed-Cap-Balanced, 70 / 25 / 5) over
``mixed_cap_universe.csv``, based at the 2026-11-13 close.
Quarterly rebalance on the last trading day of Mar/Jun/Sep/Dec; monthly
first-Monday replacement hook; divisor-style adjustments keep the level
continuous across splits and unusually large special dividends.
"""
