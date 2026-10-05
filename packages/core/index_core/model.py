"""One LightGBM classifier fitted on a chronological, purged split."""

from dataclasses import dataclass

import lightgbm as lgb
import numpy as np
import pandas as pd

from index_core.labels import LABEL_SPAN


# Rows of one stock on neighbouring days are nearly identical, so the model sees
# one day in five.
SNAPSHOT_STEP = 5
HOLDOUT_FRACTION = 0.2
MIN_HOLDOUT_DATES = 5

NUM_ROUNDS = 150
PARAMS = {
    "objective": "binary",
    "learning_rate": 0.05,
    "num_leaves": 5,
    "max_depth": 3,
    "min_data_in_leaf": 100,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 10.0,
    "seed": 7,
    "deterministic": True,
    "force_row_wise": True,
    "num_threads": 1,
    "verbose": -1,
}


@dataclass(frozen=True)
class FittedModel:
    booster: lgb.Booster
    train_rows: int
    holdout_rows: int
    holdout_auc: float | None

    def predict(self, features: pd.DataFrame) -> pd.Series:
        """Probability of beating the benchmark, indexed like `features`."""
        probabilities = self.booster.predict(features[self.booster.feature_name()])
        return pd.Series(np.asarray(probabilities), index=features.index)


def purged_split(
    calendar: pd.DatetimeIndex, dates: pd.DatetimeIndex
) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex]:
    """Split sorted label dates into an early train set and a later holdout.

    A label at day t reads closes up to day t + `LABEL_SPAN`. Training dates
    whose label window reaches the first holdout day are dropped, so no
    training label reads a price that the holdout period has already seen.
    """
    holdout_count = max(MIN_HOLDOUT_DATES, round(len(dates) * HOLDOUT_FRACTION))
    holdout = dates[-holdout_count:]
    holdout_start = calendar.get_loc(holdout[0])
    last_read = calendar.get_indexer(dates[:-holdout_count]) + LABEL_SPAN
    return dates[:-holdout_count][last_read < holdout_start], holdout


def fit(
    features: pd.DataFrame, labels: pd.Series, calendar: pd.DatetimeIndex
) -> FittedModel:
    """Fit on the early part of the history and measure on the later part.

    `features` and `labels` are indexed by (`date`, `ticker`). Rows with a
    missing feature or label are not used.
    """
    snapshots = calendar[::SNAPSHOT_STEP]
    frame = features.join(labels, how="inner").dropna()
    frame = frame[frame.index.get_level_values("date").isin(snapshots)]

    dates = pd.DatetimeIndex(sorted(frame.index.get_level_values("date").unique()))
    if len(dates) < 4 * MIN_HOLDOUT_DATES:
        raise ValueError(f"{len(dates)} labelled dates are too few to fit a model")
    train_dates, holdout_dates = purged_split(calendar, dates)

    in_train = frame.index.get_level_values("date").isin(train_dates)
    in_holdout = frame.index.get_level_values("date").isin(holdout_dates)
    train, holdout = frame[in_train], frame[in_holdout]
    columns = list(features.columns)

    booster = lgb.train(
        PARAMS,
        lgb.Dataset(train[columns], label=train["label"]),
        num_boost_round=NUM_ROUNDS,
    )
    holdout_probabilities = pd.Series(
        np.asarray(booster.predict(holdout[columns])), index=holdout.index
    )
    return FittedModel(
        booster,
        train_rows=len(train),
        holdout_rows=len(holdout),
        holdout_auc=_auc(holdout["label"], holdout_probabilities),
    )


def _auc(truth: pd.Series, score: pd.Series) -> float | None:
    """Area under the ROC curve; ties count half. None if one class is absent."""
    positives = int(truth.sum())
    negatives = len(truth) - positives
    if positives == 0 or negatives == 0:
        return None
    ranks = score.rank()
    rank_sum = float(ranks[truth == 1].sum())
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)
