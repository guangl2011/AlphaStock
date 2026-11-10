"""Tiingo-fed AS-100 / AS-MC100 daily index computation pipeline.

Methodology (design doc ``art_y1eOBDsT`` v3.0): two equal-weight price-return
indices, base 1000. AS-100 over the Mixed universe, based at the 2026-11-06
close; AS-MC100 over the Large-Cap 100, based at the 2026-11-09 close.
Quarterly rebalance on the last trading day of Mar/Jun/Sep/Dec; monthly
first-Monday replacement hook; divisor-style adjustments keep the level
continuous across splits and unusually large special dividends.
"""
