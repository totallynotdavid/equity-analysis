"""Walk-forward prediction: refit every quarter and predict only the next.

An expanding window fits on labelled snapshots whose label window closed before
the quarter began, then predicts snapshots inside that quarter. Each
prediction comes from a model that never saw the quarter's prices. The purge
also acts as the embargo: training ends before the test quarter, leaving a
`LABEL_SPAN`-day gap between them.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from index_core.labels import LABEL_SPAN, beats_benchmark
from index_core.model import labelled_snapshots, train_booster


if TYPE_CHECKING:
    from datetime import date

# About two years of weekly snapshots. Fewer cannot support a model.
MIN_TRAIN_DATES = 100


@dataclass(frozen=True)
class Fold:
    """One refit: the model trained for the quarter beginning at `test_start`."""

    test_start: pd.Timestamp
    last_train: pd.Timestamp
    train_dates: int
    train_rows: int


@dataclass(frozen=True)
class WalkForward:
    """Out-of-sample rows indexed by (`date`, `ticker`).

    The columns are `prob`, the predicted probability of beating the benchmark,
    `label` and `excess`, the realised outcome and excess return.
    """

    predictions: pd.DataFrame
    folds: list[Fold]


def walk_forward(
    features: pd.DataFrame,
    excess: pd.Series,
    calendar: pd.DatetimeIndex,
    first_oos: date | None = None,
) -> WalkForward:
    """Predict every labelled snapshot from `first_oos` on, quarter by quarter.

    `features` and `excess` are indexed by (`date`, `ticker`); `excess` is the
    return over the benchmark that `excess_returns` builds. The first test
    quarter is the first one that begins on or after `first_oos`. Without it, it
    is the first one with `MIN_TRAIN_DATES` of purged training history.
    """
    frame = labelled_snapshots(features, beats_benchmark(excess), calendar)
    frame = frame.join(excess)
    dates = frame.index.get_level_values("date")
    position = pd.Series(np.arange(len(calendar)), index=calendar)
    row_position = position.reindex(dates).to_numpy()
    columns = list(features.columns)

    quarter_starts = calendar.to_series().groupby(calendar.to_period("Q")).first()
    if first_oos is not None:
        quarter_starts = quarter_starts[quarter_starts >= pd.Timestamp(first_oos)]

    folds: list[Fold] = []
    parts: list[pd.DataFrame] = []
    for index, start in enumerate(quarter_starts):
        start_position = position[start]
        end = (
            quarter_starts.iloc[index + 1]
            if index + 1 < len(quarter_starts)
            else calendar[-1] + pd.Timedelta(days=1)
        )
        in_train = row_position + LABEL_SPAN < start_position
        in_test = (dates >= start) & (dates < end)
        train_dates = dates[in_train].unique()
        if len(train_dates) < MIN_TRAIN_DATES:
            if first_oos is not None and not folds:
                raise ValueError(
                    f"{len(train_dates)} training dates before {start.date()} "
                    f"are too few; need {MIN_TRAIN_DATES}"
                )
            continue
        if not in_test.any():
            continue

        train = frame[in_train]
        test = frame[in_test]
        booster = train_booster(train, columns)
        probabilities = np.asarray(booster.predict(test[columns]))
        parts.append(
            test[["label", "excess"]].assign(prob=probabilities)[
                ["prob", "label", "excess"]
            ]
        )
        folds.append(Fold(start, train_dates.max(), len(train_dates), len(train)))

    if not parts:
        raise ValueError("the history is too short for any walk-forward fold")
    return WalkForward(pd.concat(parts), folds)
