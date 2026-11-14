"""Closed-form statistics in plain floats — the determinism contract.

No numpy/scipy/pandas: every function here is a pure function of ordered
float sequences, so identical inputs produce identical outputs by
construction. Undefined results are ``None`` — never a silently coerced
0.0 — and callers serialize ``None`` as an empty field.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

#: During elimination, a pivot smaller than this fraction of the normal
#: matrix's largest absolute entry marks a collinear column.
_SINGULAR_TOL = 1e-10


class StatsError(ValueError):
    """Raised when the statistical inputs are inconsistent."""


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Pearson correlation of two equal-length series.

    Returns ``None`` when either series has zero variance or fewer than two
    aligned observations — an undefined correlation is empty, never 0.
    """
    if len(xs) != len(ys):
        raise StatsError(f"pearson: length mismatch ({len(xs)} vs {len(ys)})")
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = 0.0
    var_x = 0.0
    var_y = 0.0
    for x, y in zip(xs, ys, strict=True):
        dx = x - mean_x
        dy = y - mean_y
        cov += dx * dy
        var_x += dx * dx
        var_y += dy * dy
    if var_x == 0.0 or var_y == 0.0:
        return None
    return cov / math.sqrt(var_x * var_y)


def rolling_pearson(
    xs: Sequence[float], ys: Sequence[float], window: int
) -> list[float | None]:
    """Trailing-window Pearson at every position — value *i* uses only
    ``xs/max(0, i - window + 1) .. i``, so appending later days can never
    change earlier entries (no lookahead by construction).

    Positions with fewer than ``window`` aligned observations, or a
    zero-variance window, read as ``None``.
    """
    if window < 2:
        raise StatsError(f"rolling window must be >= 2, got {window}")
    if len(xs) != len(ys):
        raise StatsError(f"rolling_pearson: length mismatch ({len(xs)} vs {len(ys)})")
    out: list[float | None] = []
    for i in range(len(xs)):
        if i + 1 < window:
            # Partial window: mixing window lengths would make the early
            # values incomparable with the steady state — empty, not a
            # shorter-window estimate.
            out.append(None)
        else:
            start = i - window + 1
            out.append(pearson(xs[start : i + 1], ys[start : i + 1]))
    return out


@dataclass(frozen=True)
class OLSResult:
    """Closed-form OLS of ``y`` on an intercept plus named factors.

    ``std_errors``/``t_stats`` entries are ``None`` where the standard error
    is exactly zero (a perfect fit has no finite t-stat). ``r_squared`` is
    ``None`` when the total sum of squares is zero (constant ``y``).
    """

    intercept: float
    betas: dict[str, float]
    std_errors: dict[str, float | None]
    t_stats: dict[str, float | None]
    r_squared: float | None
    dropped_collinear: tuple[str, ...] = field(default_factory=tuple)


def ols_solve(
    y: Sequence[float], factors: dict[str, Sequence[float]]
) -> OLSResult:
    """OLS with intercept via the normal equations and Gauss-Jordan elimination.

    Greedy collinearity drop: when elimination meets a (near-)singular pivot
    on a factor column, that factor is dropped — recorded in
    ``dropped_collinear``, never silently merged — and the solve is retried.
    The intercept column is never dropped; a singular intercept raises.
    """
    n = len(y)
    names = sorted(factors)
    for name in names:
        if len(factors[name]) != n:
            raise StatsError(
                f"ols: factor {name!r} has {len(factors[name])} rows, y has {n}"
            )
    if n < 2:
        raise StatsError("ols: need at least two observations")

    dropped: list[str] = []
    while True:
        active = [name for name in names if name not in dropped]
        if not active:
            raise StatsError(
                f"ols: every factor was dropped as collinear ({dropped})"
            )
        matrix, rhs, columns = _normal_matrix(y, active, factors)
        scale = max((abs(v) for row in matrix for v in row), default=0.0)
        try:
            beta_vec, inverse = _solve_with_inverse(matrix, rhs, scale)
        except _SingularError as exc:
            column = columns[exc.column]
            if column is None:
                raise StatsError("ols: intercept column is singular") from exc
            dropped.append(column)
            continue
        return _assemble(y, active, factors, beta_vec, inverse, tuple(dropped))


class _SingularError(Exception):
    def __init__(self, column: int) -> None:
        super().__init__(f"singular pivot at column {column}")
        self.column = column


def _normal_matrix(
    y: Sequence[float], active: list[str], factors: dict[str, Sequence[float]]
) -> tuple[list[list[float]], list[float], list[str | None]]:
    """X'X, X'y and the variable each column stands for (None = intercept)."""
    n = len(y)
    columns: list[str | None] = [None, *active]
    k = len(columns)
    # Column-major x-values: x_cols[j][i] is observation i of column j.
    x_cols: list[list[float]] = [[1.0] * n]
    for name in active:
        x_cols.append(list(factors[name]))
    matrix = [[0.0] * k for _ in range(k)]
    for a in range(k):
        for b in range(a, k):
            s = math.fsum(x * w for x, w in zip(x_cols[a], x_cols[b], strict=True))
            matrix[a][b] = s
            matrix[b][a] = s
    rhs = [math.fsum(w * v for w, v in zip(x_cols[j], y, strict=True)) for j in range(k)]
    return matrix, rhs, columns


def _solve_with_inverse(
    matrix: list[list[float]], rhs: list[float], scale: float
) -> tuple[list[float], list[list[float]]]:
    """Gauss-Jordan on [M | rhs | I]; returns (beta, M^-1).

    Raises :class:`_SingularError` naming the first column whose pivot is
    negligible relative to ``scale``.
    """
    k = len(rhs)
    aug = [
        [
            *matrix[i],
            *[1.0 if j == i else 0.0 for j in range(k)],
            rhs[i],
        ]
        for i in range(k)
    ]
    for col in range(k):
        pivot_row = max(range(col, k), key=lambda r: abs(aug[r][col]))
        pivot = abs(aug[pivot_row][col])
        if pivot <= _SINGULAR_TOL * scale or pivot == 0.0:
            raise _SingularError(col)
        aug[col], aug[pivot_row] = aug[pivot_row], aug[col]
        inv = 1.0 / aug[col][col]
        aug[col] = [v * inv for v in aug[col]]
        for r in range(k):
            if r == col:
                continue
            factor = aug[r][col]
            if factor != 0.0:
                aug[r] = [v - factor * w for v, w in zip(aug[r], aug[col], strict=True)]
    beta = [aug[i][2 * k] for i in range(k)]
    inverse = [[aug[i][k + j] for j in range(k)] for i in range(k)]
    return beta, inverse


def _assemble(
    y: Sequence[float],
    active: list[str],
    factors: dict[str, Sequence[float]],
    beta_vec: list[float],
    inverse: list[list[float]],
    dropped: tuple[str, ...],
) -> OLSResult:
    n = len(y)
    intercept = beta_vec[0]
    betas = {name: beta_vec[i + 1] for i, name in enumerate(active)}

    residuals: list[float] = []
    for i in range(n):
        fitted = intercept + sum(factors[name][i] * betas[name] for name in active)
        residuals.append(y[i] - fitted)

    rss = math.fsum(r * r for r in residuals)
    mean_y = sum(y) / n
    tss = math.fsum((v - mean_y) ** 2 for v in y)

    dof = n - len(active) - 1
    if dof <= 0:
        raise StatsError(
            f"ols: {n} observations cannot estimate {len(active) + 1} parameters"
        )
    sigma2 = rss / dof

    std_errors: dict[str, float | None] = {}
    t_stats: dict[str, float | None] = {}
    for i, name in enumerate(active):
        variance = sigma2 * inverse[i + 1][i + 1]
        # Numerical noise can push a near-perfect fit's variance negative;
        # clamp to zero rather than emit a negative standard error.
        se = math.sqrt(max(variance, 0.0))
        std_errors[name] = se
        t_stats[name] = None if se == 0.0 else betas[name] / se

    r_squared: float | None
    if tss == 0.0:
        r_squared = None
    else:
        r_squared = 1.0 - rss / tss

    return OLSResult(
        intercept=intercept,
        betas=betas,
        std_errors=std_errors,
        t_stats=t_stats,
        r_squared=r_squared,
        dropped_collinear=dropped,
    )
