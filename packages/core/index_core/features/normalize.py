from typing import TYPE_CHECKING


if TYPE_CHECKING:
    import pandas as pd


def rank_by_date(features: pd.DataFrame) -> pd.DataFrame:
    """Replace each value with its percentile among the tickers of the same date.

    A feature's raw level drifts with the market regime. Its rank within one day
    does not, and it reads only that day.
    """
    return features.groupby(level="date").rank(pct=True)
