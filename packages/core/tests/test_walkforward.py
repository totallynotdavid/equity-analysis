from datetime import date

import numpy as np
import pandas as pd
import pytest

from index_core.evaluation import evaluate
from index_core.features.normalize import rank_by_date
from index_core.features.technical import technical_features
from index_core.labels import LABEL_SPAN, excess_returns
from index_core.walkforward import MIN_TRAIN_DATES, WalkForward, walk_forward


@pytest.fixture(scope="module")
def features(planted: pd.DataFrame) -> pd.DataFrame:
    return rank_by_date(technical_features(planted, "SPY"))


@pytest.fixture(scope="module")
def excess(planted: pd.DataFrame) -> pd.Series:
    return excess_returns(planted, "SPY")


@pytest.fixture(scope="module")
def calendar(planted: pd.DataFrame) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(sorted(planted["date"].unique()))


@pytest.fixture(scope="module")
def result(
    features: pd.DataFrame, excess: pd.Series, calendar: pd.DatetimeIndex
) -> WalkForward:
    return walk_forward(features, excess, calendar)


def _shuffled_within_dates(excess: pd.Series, seed: int) -> pd.Series:
    """The same excess returns dealt to other stocks of the same date."""
    rng = np.random.default_rng(seed)
    return excess.groupby(level="date").transform(
        lambda day: rng.permutation(day.to_numpy())
    )


def test_no_training_label_window_reaches_into_the_test_quarter(
    result: WalkForward, calendar: pd.DatetimeIndex
) -> None:
    assert len(result.folds) > 20
    for fold in result.folds:
        last_read = calendar.searchsorted(fold.last_train) + LABEL_SPAN
        assert last_read < calendar.searchsorted(fold.test_start)
    # The window grows by one quarter at a time.
    sizes = [fold.train_dates for fold in result.folds]
    assert sizes == sorted(sizes)
    assert sizes[0] >= MIN_TRAIN_DATES


def test_each_prediction_comes_from_the_model_of_its_own_quarter(
    result: WalkForward,
) -> None:
    dates = pd.DatetimeIndex(result.predictions.index.get_level_values("date"))
    starts = [fold.test_start for fold in result.folds]
    quarter = dates.to_period("Q")
    assert quarter.nunique() == len(starts)
    assert dates.min() >= starts[0]
    assert list(starts) == sorted(set(starts))


def test_labels_inside_the_purge_window_cannot_change_a_quarters_predictions(
    features: pd.DataFrame,
    excess: pd.Series,
    calendar: pd.DatetimeIndex,
) -> None:
    base = walk_forward(features, excess, calendar, date(2020, 1, 1))
    start = calendar.searchsorted(base.folds[0].test_start)
    position = calendar.get_indexer(excess.index.get_level_values("date"))
    rng = np.random.default_rng(3)

    # These labels read prices of the test quarter or later.
    reaching = position + LABEL_SPAN >= start
    garbage = excess.copy()
    garbage[reaching] = rng.normal(0, 0.1, reaching.sum())
    after = walk_forward(features, garbage, calendar, date(2020, 1, 1))

    # Later quarters may train on the garbage, so only the first one is compared.
    first_quarter = (
        base.predictions.index.get_level_values("date") < base.folds[1].test_start
    )
    expected = base.predictions.loc[first_quarter, "prob"]
    pd.testing.assert_series_equal(
        after.predictions.loc[expected.index, "prob"], expected
    )

    # A control: labels the first model does train on do change it.
    changed = excess.copy()
    changed[~reaching] = rng.normal(0, 0.1, (~reaching).sum())
    control = walk_forward(features, changed, calendar, date(2020, 1, 1))
    assert not np.allclose(control.predictions.loc[expected.index, "prob"], expected)


def test_a_planted_signal_gives_a_clearly_positive_rank_ic(
    result: WalkForward, calendar: pd.DatetimeIndex
) -> None:
    evaluation = evaluate(result.predictions, calendar)

    assert evaluation.mean_ic.low > 0.05
    assert evaluation.ic_t_stat > 3
    assert evaluation.mean_auc.low > 0.5
    assert evaluation.spread.low > 0


def test_shuffled_labels_give_no_significant_rank_ic(
    features: pd.DataFrame, excess: pd.Series, calendar: pd.DatetimeIndex
) -> None:
    shuffled = _shuffled_within_dates(excess, seed=1)

    evaluation = evaluate(
        walk_forward(features, shuffled, calendar).predictions, calendar
    )

    assert abs(evaluation.ic_t_stat) < 2
    assert evaluation.mean_ic.low < 0 < evaluation.mean_ic.high


def test_first_oos_picks_the_first_quarter_that_begins_on_or_after_it(
    features: pd.DataFrame, excess: pd.Series, calendar: pd.DatetimeIndex
) -> None:
    late = walk_forward(features, excess, calendar, date(2019, 2, 15))

    assert late.folds[0].test_start == pd.Timestamp("2019-04-01")
    assert (
        late.predictions.index.get_level_values("date").min()
        >= late.folds[0].test_start
    )


def test_a_first_oos_with_too_little_history_before_it_is_refused(
    features: pd.DataFrame, excess: pd.Series, calendar: pd.DatetimeIndex
) -> None:
    with pytest.raises(ValueError, match="training dates"):
        walk_forward(features, excess, calendar, date(2013, 1, 1))


def test_a_history_too_short_for_any_fold_is_refused(
    features: pd.DataFrame, excess: pd.Series, calendar: pd.DatetimeIndex
) -> None:
    early = calendar[:500]
    on_early = features.index.get_level_values("date").isin(early)

    with pytest.raises(ValueError, match="too short"):
        walk_forward(
            features[on_early],
            excess[excess.index.get_level_values("date").isin(early)],
            early,
        )
