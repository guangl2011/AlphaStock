"""AlphaStock correlation & factor-relationship model.

Deterministic, pure-stdlib quantification of how the AS-100 / AS-MCG100 / AS-MCB100
indices and each constituent's price relate to factor series (ETF-proxy
benchmark spreads) and to the platform's V1–V4 signal events. Run with
``python -m pipelines.correlation``; method docs: ``docs/correlation-model.md``.
"""

from .factors import FACTOR_NAMES, REGRESSION_FACTORS, build_factors
from .model import (
    ESTIMATION_WINDOW,
    EVENT_AFTER,
    WINDOW_LONG,
    WINDOW_SHORT,
    aggregate_ars,
    event_abnormal_returns,
    index_factor_exposures,
    stock_factor_rows,
)
from .stats import pearson, rolling_pearson

__all__ = [
    "EVENT_AFTER",
    "ESTIMATION_WINDOW",
    "FACTOR_NAMES",
    "REGRESSION_FACTORS",
    "WINDOW_LONG",
    "WINDOW_SHORT",
    "aggregate_ars",
    "build_factors",
    "event_abnormal_returns",
    "index_factor_exposures",
    "pearson",
    "rolling_pearson",
    "stock_factor_rows",
]
