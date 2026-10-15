"""Whether an edge could be luck: the Deflated Sharpe Ratio and the factor alpha."""

from dataclasses import dataclass
from statistics import NormalDist
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from index_core.portfolio import MONTHS_PER_YEAR
from index_core.sources.base import FACTOR_COLUMNS


if TYPE_CHECKING:
    from collections.abc import Sequence

    from index_core.portfolio import Holdings

_NORMAL = NormalDist()
_EULER_GAMMA = 0.5772156649015329
# The alpha regression needs at least 12 overlapping months. Fewer months do
# not support a regression on the six factor columns.
MIN_ALPHA_MONTHS = 12
ALPHA_LAGS = 3


@dataclass(frozen=True)
class Deflated:
    """Bailey and López de Prado's Deflated Sharpe Ratio.

    Sharpe ratios are per month. `benchmark` is the best Sharpe ratio that
    `trials` strategies with no edge would reach by luck, and `probability` is
    the chance that the true Sharpe ratio is above it.
    """

    sharpe: float
    benchmark: float
    probability: float
    trials: int


def deflated_sharpe(returns: np.ndarray, trial_sharpes: Sequence[float]) -> Deflated:
    """The Deflated Sharpe Ratio of monthly `returns`.

    `trial_sharpes` holds the monthly Sharpe ratio of every specification ever
    tried, this one included. With a single trial nothing is deflated.
    """
    count = len(returns)
    centred = returns - returns.mean()
    sigma = float(np.sqrt(np.mean(centred**2)))
    sharpe = float(returns.mean() / returns.std(ddof=1))
    skew = float(np.mean(centred**3) / sigma**3)
    kurtosis = float(np.mean(centred**4) / sigma**4)

    trials = len(trial_sharpes)
    spread = float(np.var(trial_sharpes, ddof=1)) if trials > 1 else 0.0
    benchmark = (
        float(np.sqrt(spread))
        * (
            (1 - _EULER_GAMMA) * _NORMAL.inv_cdf(1 - 1 / trials)
            + _EULER_GAMMA * _NORMAL.inv_cdf(1 - 1 / (trials * np.e))
        )
        if trials > 1
        else 0.0
    )
    variance = 1 - skew * sharpe + (kurtosis - 1) / 4 * sharpe**2
    z = (sharpe - benchmark) * np.sqrt(count - 1) / np.sqrt(variance)
    return Deflated(sharpe, benchmark, _NORMAL.cdf(float(z)), trials)


@dataclass(frozen=True)
class Alpha:
    """A monthly return regressed on the Fama-French factors, with HAC errors."""

    monthly: float
    annual: float
    t_stat: float
    r2: float
    loadings: dict[str, float]
    months: int


def factor_alpha(
    returns: np.ndarray, holdings: Holdings, factors: pd.DataFrame
) -> Alpha | None:
    """The alpha of a self-financing strategy's monthly `returns` on the factors.

    `returns` has one entry per holding period of `holdings`. A period counts
    only if the factor file has every trading day of it, because the file
    trails the present by weeks. None if fewer than `MIN_ALPHA_MONTHS` count.
    """
    exposures = _period_factors(holdings, factors)
    covered = exposures.notna().all(axis=1).to_numpy()
    if covered.sum() < MIN_ALPHA_MONTHS:
        return None
    design = np.column_stack(
        [np.ones(covered.sum()), exposures[covered].to_numpy(dtype=float)]
    )
    fit = _ols_hac(returns[covered], design, ALPHA_LAGS)
    return Alpha(
        monthly=float(fit.coefficients[0]),
        annual=float(MONTHS_PER_YEAR * fit.coefficients[0]),
        t_stat=float(fit.coefficients[0] / fit.errors[0]),
        r2=fit.r2,
        loadings=dict(
            zip(exposures.columns, map(float, fit.coefficients[1:]), strict=True)
        ),
        months=int(covered.sum()),
    )


def _period_factors(holdings: Holdings, factors: pd.DataFrame) -> pd.DataFrame:
    """Each factor compounded over every holding period; NaN where the file
    lacks a trading day. The market column is the return above the risk-free."""
    days = factors.index
    rows = []
    for entry, exit_, length in zip(
        holdings.entry, holdings.exit, holdings.days, strict=True
    ):
        window = factors[(days > entry) & (days <= exit_)]
        if len(window) < length:
            rows.append(dict.fromkeys(FACTOR_COLUMNS[:-1], np.nan))
            continue
        market = _compound(window["mkt_rf"] + window["rf"]) - _compound(window["rf"])
        rows.append(
            {
                "mkt_rf": market,
                **{name: _compound(window[name]) for name in FACTOR_COLUMNS[1:-1]},
            }
        )
    return pd.DataFrame(rows)


def _compound(returns: pd.Series) -> float:
    return float(np.prod(1 + returns.to_numpy(dtype=float)) - 1)


@dataclass(frozen=True)
class _Fit:
    coefficients: np.ndarray
    errors: np.ndarray
    r2: float


def _ols_hac(y: np.ndarray, design: np.ndarray, lags: int) -> _Fit:
    """Least squares with Newey-West (Bartlett) errors, scaled by n / (n - k)."""
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
    return _Fit(
        coefficients,
        np.sqrt(np.diag(covariance)),
        1 - float(residual @ residual) / total if total > 0 else float("nan"),
    )
