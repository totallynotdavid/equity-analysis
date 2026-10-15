"""Rank information coefficient of each feature alone.

A feature that does not rank stocks by their next excess return gives a model
nothing to find. The table is the evidence for a feature before any model
touches it. It fits nothing and so costs no trial.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import numpy as np
import pandas as pd

from index_core.evaluation import (
    MIN_NAMES,
    NEWEY_WEST_LAGS,
    Estimate,
    mean_by_month_block,
    newey_west_t,
)
from index_core.features.technical import FEATURES as TECHNICAL


if TYPE_CHECKING:
    from collections.abc import Sequence

# The feature hurdle is 3.0 because repeated trials make a nominal t-statistic
# of 2 insufficient evidence for a new factor.
HURDLE_T = 3.0


@dataclass(frozen=True)
class FeatureIC:
    feature: str
    family: Literal["technical", "fundamental"]
    mean_ic: Estimate
    t_stat: float
    dates: int


def feature_ics(rows: pd.DataFrame, features: Sequence[str]) -> list[FeatureIC]:
    """Mean Spearman correlation of each feature with `excess`, date by date.

    `rows` is indexed by (`date`, `ticker`) and holds the ranked `features` and
    the realised `excess` return. A date counts for a feature when at least
    `MIN_NAMES` stocks have both. The interval resamples whole months, and the
    t-statistic is Newey-West like the model's.
    """
    columns = list(features)
    per_date = pd.DataFrame(
        {date: _spearman(group, columns) for date, group in rows.groupby(level="date")}
    ).T.sort_index()
    estimates = mean_by_month_block(per_date)
    return [
        FeatureIC(
            feature=name,
            family="technical" if name in TECHNICAL else "fundamental",
            mean_ic=estimate,
            t_stat=_t_stat(per_date[name]),
            dates=int(per_date[name].count()),
        )
        for name, estimate in zip(columns, estimates, strict=True)
    ]


def _spearman(group: pd.DataFrame, columns: list[str]) -> pd.Series:
    """One date's correlations. Columns without a gap are ranked together; a
    column with a gap is correlated on the stocks that have it."""
    excess = group["excess"]
    values = group[columns]
    present = values.notna().sum()
    whole = present[(present == len(group)) & (present >= MIN_NAMES)].index
    result = pd.Series(np.nan, index=columns)
    if excess.notna().all() and len(whole):
        result[whole] = values[whole].rank().corrwith(excess.rank())
    for name in present.index.difference(whole):
        if present[name] >= MIN_NAMES:
            result[name] = values[name].corr(excess, method="spearman")
    return result


def _t_stat(series: pd.Series) -> float:
    values = series.dropna().to_numpy()
    if len(values) < 2 or values.std() == 0:
        return float("nan")
    return newey_west_t(values, NEWEY_WEST_LAGS)
