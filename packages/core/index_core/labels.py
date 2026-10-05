"""The training label: does a stock beat the benchmark over the next 63 days?"""

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    import pandas as pd


HORIZON = 63
# A signal computed from the close of day t is traded at the close of day t + 1.
ENTRY_LAG = 1
# Position of the last close a label at day t reads, counted from t.
LABEL_SPAN = ENTRY_LAG + HORIZON


def excess_return_labels(prices: pd.DataFrame, benchmark: str) -> pd.Series:
    """Label 1.0 where a stock's return beats the benchmark's, else 0.0.

    Both returns run from the close at t + 1 to the close at t + 64 in adjusted
    prices, counted in rows of the benchmark's calendar. The last 64 dates have
    no label, and neither has a date where either close is missing.
    The result is indexed by (`date`, `ticker`).
    """
    adj_close = prices.pivot(index="date", columns="ticker", values="adj_close")
    adj_close = adj_close.reindex(adj_close[benchmark].dropna().index)

    forward = adj_close.shift(-LABEL_SPAN) / adj_close.shift(-ENTRY_LAG) - 1
    excess = forward.drop(columns=benchmark).sub(forward[benchmark], axis=0)

    labels = (excess > 0).astype(float).where(excess.notna())
    return labels.stack().dropna().rename("label").rename_axis(["date", "ticker"])
