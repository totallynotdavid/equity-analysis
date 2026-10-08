"""The training label: does a stock beat the benchmark over the next 63 days?"""

from typing import TYPE_CHECKING, cast


if TYPE_CHECKING:
    import pandas as pd


HORIZON = 63
# A signal computed from the close of day t is traded at the close of day t + 1.
ENTRY_LAG = 1
# Position of the last close a label at day t reads, counted from t.
LABEL_SPAN = ENTRY_LAG + HORIZON


def excess_returns(prices: pd.DataFrame, benchmark: str) -> pd.Series:
    """A stock's return minus the benchmark's over the label window.

    Both returns run from the close at t + 1 to the close at t + 64 in adjusted
    prices, counted in rows of the benchmark's calendar. The last 64 dates have
    no value, and neither has a date where either close is missing.
    The result is indexed by (`date`, `ticker`).
    """
    adj_close = prices.pivot(index="date", columns="ticker", values="adj_close")
    adj_close = adj_close.reindex(adj_close[benchmark].dropna().index)

    forward = adj_close.shift(-LABEL_SPAN) / adj_close.shift(-ENTRY_LAG) - 1
    excess = forward.drop(columns=benchmark).sub(forward[benchmark], axis=0)
    stacked = cast("pd.Series", excess.stack().dropna())
    return stacked.rename("excess").rename_axis(["date", "ticker"])


def beats_benchmark(excess: pd.Series) -> pd.Series:
    """1.0 where the excess return is positive, else 0.0."""
    return (excess > 0).astype(float).rename("label")


def excess_return_labels(prices: pd.DataFrame, benchmark: str) -> pd.Series:
    """Label 1.0 where a stock's return beats the benchmark's, else 0.0."""
    return beats_benchmark(excess_returns(prices, benchmark))
