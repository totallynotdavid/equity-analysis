"""What a stock behaves like: its beta, volatility, drawdown and factor exposure.

`assess` reads the last `WINDOW` trading days for which a stock has a return and
the Fama-French file has factor returns. The file trails the present by weeks,
so the window ends where the file does, and every figure describes the same
days. Two regressions use them, both with Newey-West errors so that
heteroskedastic and autocorrelated returns do not make the intervals too narrow:

- the daily return on SPY's, which is the beta;
- the daily return above the risk-free rate on the five factors, which gives the
  loadings, the annualised alpha and the R².

An interval is the estimate plus or minus 1.96 standard errors. The errors are
asymptotic, which a window of three years of days supports.
"""

from statistics import NormalDist
from typing import Literal

import numpy as np
import pandas as pd

from index_core.regression import Fit, newey_west_lags, ols_hac
from index_core.report import Estimate, Loading, Risk, RiskFit
from index_core.sources.base import FACTOR_COLUMNS


# Use three years of trading days. This gives six coefficients usable intervals
# while limiting the window's exposure to changes in the company's business.
WINDOW = 756
TRADING_DAYS = 252
FACTORS = FACTOR_COLUMNS[:-1]
_Z95 = NormalDist().inv_cdf(0.975)


def daily_returns(adj_close: pd.DataFrame) -> pd.DataFrame:
    """Return each column from the previous row, with gaps marked as NaN."""
    return adj_close / adj_close.shift(1) - 1


def assess(
    stock: pd.Series[float],
    market: pd.Series[float],
    factors: pd.DataFrame,
    window: int = WINDOW,
) -> Risk:
    """The risk of a stock, or why there is none.

    `stock` and `market` are daily total returns by date, `market` being SPY's,
    and `factors` is the stored factor file. Days missing from any of the three
    are skipped.
    """
    if factors.empty:
        return _refused(
            "insufficient_history",
            "no factor returns are stored; run `eq factors`",
            window,
        )
    joined = (
        pd.concat({"stock": stock, "market": market}, axis=1, sort=True)
        .join(factors, how="inner")
        .dropna()
    )
    if len(joined) < window:
        return _refused(
            "insufficient_history",
            f"{len(joined)} days have both a return and factor returns, "
            f"and the window is {window}",
            window,
        )
    days = joined.iloc[-window:]
    returns = days["stock"].to_numpy(dtype=float)
    ones = np.ones(window)
    lags = newey_west_lags(window)
    try:
        beta = ols_hac(returns, np.column_stack([ones, days["market"]]), lags)
        factor_fit = ols_hac(
            returns - days["rf"].to_numpy(dtype=float),
            np.column_stack([ones, days[list(FACTORS)]]),
            lags,
        )
    except np.linalg.LinAlgError:
        return _refused(
            "not_estimable", "the market or factor returns are collinear", window
        )
    if not np.isfinite(factor_fit.r2):
        return _refused("not_estimable", "the stock's return does not vary", window)

    return Risk(
        status="ok",
        window=window,
        fit=RiskFit(
            start=days.index[0].date(),
            end=days.index[-1].date(),
            beta=_estimate(beta, 1),
            volatility=float(np.std(returns, ddof=1) * np.sqrt(TRADING_DAYS)),
            max_drawdown=max_drawdown(returns),
            r2=factor_fit.r2,
            alpha=_estimate(factor_fit, 0, TRADING_DAYS),
            loadings=[
                Loading(factor=name, **_estimate(factor_fit, position).model_dump())
                for position, name in enumerate(FACTORS, start=1)
            ],
        ),
    )


def max_drawdown(returns: np.ndarray) -> float:
    """Return the largest fall from a running peak as a negative fraction."""
    wealth = np.concatenate([[1.0], np.cumprod(1 + returns)])
    return float(np.min(wealth / np.maximum.accumulate(wealth) - 1))


def _estimate(fit: Fit, position: int, scale: float = 1.0) -> Estimate:
    """The coefficient at `position`, times `scale`, with its interval."""
    value = float(fit.coefficients[position]) * scale
    error = float(fit.errors[position]) * scale
    return Estimate(
        value=value, se=error, lo=value - _Z95 * error, hi=value + _Z95 * error
    )


def _refused(
    status: Literal["insufficient_history", "not_estimable"], reason: str, window: int
) -> Risk:
    return Risk(status=status, reason=reason, window=window)
