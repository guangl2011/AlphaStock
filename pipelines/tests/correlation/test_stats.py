"""Known-answer tests for the closed-form statistics.

Every case here is hand-checkable: exact +/-1/0 correlations, zero
variance -> None (never 0), exact OLS recovery, and the no-lookahead
property of trailing windows.
"""

from __future__ import annotations

import dataclasses

import pytest

from pipelines.correlation.stats import (
    OLSResult,
    StatsError,
    ols_solve,
    pearson,
    rolling_pearson,
)


def test_pearson_exact_positive_one():
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert pearson(xs, xs) == pytest.approx(1.0, abs=1e-12)


def test_pearson_exact_negative_one():
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert pearson(xs, [-x for x in xs]) == pytest.approx(-1.0, abs=1e-12)


def test_pearson_exact_zero():
    # Orthogonal patterns: cov = (-1)(1) + (1)(1) + (-1)(-1) + (1)(-1) = 0.
    xs = [-1.0, 1.0, -1.0, 1.0]
    ys = [1.0, 1.0, -1.0, -1.0]
    assert pearson(xs, ys) == pytest.approx(0.0, abs=1e-12)


def test_pearson_zero_variance_is_none_not_zero():
    # A constant series has undefined correlation — empty, never 0.
    assert pearson([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None
    assert pearson([1.0, 2.0, 3.0], [5.0, 5.0, 5.0]) is None


def test_pearson_too_few_or_mismatched_observations():
    assert pearson([1.0], [2.0]) is None
    with pytest.raises(StatsError, match="length mismatch"):
        pearson([1.0, 2.0], [1.0])


def test_rolling_pearson_is_purely_trailing_no_lookahead():
    # Appending day t+1 must leave every earlier rolling value unchanged.
    xs = [0.01, -0.005, 0.02, 0.0, 0.015, -0.01, 0.005, 0.03, -0.002, 0.01]
    ys = [x * 0.5 + 0.001 * i for i, x in enumerate(xs)]
    ys[-1] += 0.05  # the appended day's shock
    full = rolling_pearson(xs, ys, 4)
    for cut in range(2, len(xs)):
        prefix = rolling_pearson(xs[:cut], ys[:cut], 4)
        assert prefix == full[:cut]


def test_rolling_pearson_below_window_is_none():
    out = rolling_pearson([1.0, 2.0, 3.0], [1.0, 2.0, 3.0], 5)
    assert out == [None, None, None]


def test_ols_recovers_exact_linear_combination():
    # y = 0.7*MKT + 0.3*BREADTH (no intercept, no noise): the closed-form
    # solve must recover both betas and R^2 = 1.0.
    mkt = [0.01, -0.004, 0.012, 0.002, -0.009, 0.006, 0.015, -0.002]
    breadth = [0.003, 0.008, -0.005, 0.011, 0.001, -0.007, 0.004, 0.009]
    y = [0.7 * m + 0.3 * b for m, b in zip(mkt, breadth, strict=False)]
    result = ols_solve(y, {"MKT": mkt, "BREADTH": breadth})
    assert result.betas["MKT"] == pytest.approx(0.7, abs=1e-9)
    assert result.betas["BREADTH"] == pytest.approx(0.3, abs=1e-9)
    assert result.r_squared == pytest.approx(1.0, abs=1e-9)


def test_ols_recovers_beta_and_intercept_with_noise():
    mkt = [0.01, -0.004, 0.012, 0.002, -0.009, 0.006, 0.015, -0.002, 0.007, 0.0]
    y = [1.5 * m + 0.002 for m in mkt]
    result = ols_solve(y, {"MKT": mkt})
    assert result.betas["MKT"] == pytest.approx(1.5, abs=1e-9)
    assert result.intercept == pytest.approx(0.002, abs=1e-9)
    # A perfect fit has (numerically) zero standard error; the t-stat is
    # either None (exactly zero) or astronomically large — never a
    # plausible finite number.
    assert result.std_errors["MKT"] == pytest.approx(0.0, abs=1e-12)
    t_stat = result.t_stats["MKT"]
    assert t_stat is None or abs(t_stat) > 1e10


def test_ols_drops_collinear_duplicate_and_reports_it():
    mkt = [0.01, -0.004, 0.012, 0.002, -0.009, 0.006]
    y = [0.5 * m for m in mkt]
    result = ols_solve(y, {"MKT": mkt, "MKT_DUP": list(mkt)})
    assert result.dropped_collinear == ("MKT_DUP",)
    assert set(result.betas) == {"MKT"}
    assert result.betas["MKT"] == pytest.approx(0.5, abs=1e-9)


def test_ols_collinear_drop_is_greedy_until_solvable():
    # Two duplicate pairs: both duplicates must be dropped, in order.
    a = [0.01, -0.004, 0.012, 0.002, -0.009, 0.006]
    b = [0.003, 0.008, -0.005, 0.011, 0.001, -0.007]
    y = [0.4 * x + 0.2 * v for x, v in zip(a, b, strict=False)]
    result = ols_solve(y, {"A": list(a), "A2": list(a), "B": list(b), "B2": list(b)})
    assert result.dropped_collinear == ("A2", "B2")
    assert set(result.betas) == {"A", "B"}
    assert result.r_squared == pytest.approx(1.0, abs=1e-9)


def test_ols_constant_y_has_undefined_r_squared():
    mkt = [0.01, -0.004, 0.012, 0.002]
    result = ols_solve([0.01] * 4, {"MKT": mkt})
    assert result.r_squared is None


def test_ols_rejects_indeterminate_system():
    mkt = [0.01, -0.004]
    with pytest.raises(StatsError, match="cannot estimate"):
        ols_solve([0.01, 0.0], {"MKT": mkt, "OTHER": [1.0, 1.0]})


def test_ols_result_is_immutable():
    result = OLSResult(
        intercept=0.0, betas={}, std_errors={}, t_stats={}, r_squared=None
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.intercept = 1.0  # type: ignore[misc]
