"""Least squares with heteroskedasticity- and autocorrelation-robust errors."""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Fit:
    coefficients: np.ndarray
    errors: np.ndarray
    r2: float


def newey_west_lags(rows: int) -> int:
    """The Bartlett bandwidth Newey and West suggest for `rows` observations:
    `4 (rows / 100)^(2/9)`, rounded down."""
    return int(4 * (rows / 100) ** (2 / 9))


def ols_hac(y: np.ndarray, design: np.ndarray, lags: int) -> Fit:
    """Least squares with Newey-West (Bartlett) errors, scaled by n / (n - k).

    Raises `np.linalg.LinAlgError` where the columns of `design` are collinear.
    """
    rows, columns = design.shape
    inverse = np.linalg.inv(design.T @ design)
    coefficients = inverse @ design.T @ y
    residual = y - design @ coefficients
    scores = design * residual[:, None]
    middle = scores.T @ scores
    for lag in range(1, min(lags, rows - 1) + 1):
        cross = scores[lag:].T @ scores[:-lag]
        middle += (1 - lag / (lags + 1)) * (cross + cross.T)
    covariance = rows / (rows - columns) * inverse @ middle @ inverse
    total = float(((y - y.mean()) ** 2).sum())
    return Fit(
        coefficients,
        np.sqrt(np.diag(covariance)),
        1 - float(residual @ residual) / total if total > 0 else float("nan"),
    )
