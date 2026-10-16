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


def family_of(feature: str) -> Literal["technical", "fundamental"]:
    return "technical" if feature in TECHNICAL else "fundamental"


def feature_ics(rows: pd.DataFrame, features: Sequence[str]) -> list[FeatureIC]:
    """Mean Spearman correlation of each feature with `excess`, date by date.

    `rows` is indexed by (`date`, `ticker`) and holds the ranked `features` and
    the realised `excess` return. A date counts for a feature when at least
    `MIN_NAMES` stocks have both. The interval resamples whole months, and the
    t-statistic is Newey-West like the model's.
    """
    columns = list(features)
    per_date = pd.DataFrame(
        {name: _spearman_by_date(rows, name) for name in columns}
    ).sort_index()
    estimates = mean_by_month_block(per_date)
    return [
        FeatureIC(
            feature=name,
            family=family_of(name),
            mean_ic=estimate,
            t_stat=_t_stat(per_date[name]),
            dates=int(per_date[name].count()),
        )
        for name, estimate in zip(columns, estimates, strict=True)
    ]


def _spearman_by_date(rows: pd.DataFrame, name: str) -> pd.Series:
    """Each date's Spearman correlation of one feature with `excess`, on the
    stocks that have both. A date where fewer than `MIN_NAMES` stocks have the
    feature has none. Ranks tie at their mean, as in `Series.corr`."""
    dates = rows.index.get_level_values("date")
    counts = rows[name].notna().astype(int).groupby(dates).sum()
    pair = rows[[name, "excess"]].dropna()
    ranks = pair.groupby(level="date").rank()
    centred = ranks - ranks.groupby(level="date").transform("mean")
    num = (centred[name] * centred["excess"]).groupby(level="date").sum()
    spread = (centred**2).groupby(level="date").sum()
    den = pd.Series(np.sqrt(spread[name] * spread["excess"]), index=spread.index)
    ic = (num / den.where(den > 0)).reindex(counts.index)
    return ic.where(counts >= MIN_NAMES).rename(name)


def _t_stat(series: pd.Series) -> float:
    values = series.dropna().to_numpy()
    if len(values) < 2 or values.std() == 0:
        return float("nan")
    return newey_west_t(values, NEWEY_WEST_LAGS)
