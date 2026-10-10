from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from index_core.features.normalize import rank_by_date
from index_core.features.technical import technical_features
from index_core.labels import LABEL_SPAN, excess_return_labels
from index_core.model import (
    SNAPSHOT_STEP,
    MissingRuntimeError,
    fit,
    load_lightgbm,
    purged_split,
)
from index_core.scoring import decile_scores
from membership import of_prices


def _without_openmp(monkeypatch: pytest.MonkeyPatch, platform: str = "linux") -> None:
    def refuse(name: str) -> None:
        raise OSError(f"{name}: cannot open shared object file")

    monkeypatch.setattr(
        "index_core.model.importlib", SimpleNamespace(import_module=refuse)
    )
    monkeypatch.setattr("index_core.model.sys", SimpleNamespace(platform=platform))


def test_a_missing_openmp_runtime_names_the_package_to_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _without_openmp(monkeypatch)

    with pytest.raises(MissingRuntimeError, match="apt-get install libgomp1"):
        load_lightgbm()


def test_a_missing_openmp_runtime_on_macos_names_libomp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _without_openmp(monkeypatch, "darwin")

    with pytest.raises(MissingRuntimeError, match="brew install libomp"):
        load_lightgbm()


def test_no_training_label_window_reaches_the_holdout_period() -> None:
    calendar = pd.bdate_range("2020-01-01", periods=800)
    dates = calendar[::SNAPSHOT_STEP]

    train, holdout = purged_split(calendar, dates)

    assert len(train) > 0
    assert train.max() < holdout.min()
    last_train_read = calendar.searchsorted(train.max()) + LABEL_SPAN
    assert last_train_read < calendar.searchsorted(holdout.min())
    # The purge drops the dates in between and nothing else.
    first_holdout = calendar.searchsorted(holdout.min())
    dropped = [d for d in dates if d < holdout.min() and d not in train]
    assert all(calendar.searchsorted(d) + LABEL_SPAN >= first_holdout for d in dropped)


def test_fit_uses_early_dates_for_training_and_late_dates_for_the_holdout(
    prices: pd.DataFrame,
) -> None:
    features = rank_by_date(technical_features(prices, "SPY"))
    labels = excess_return_labels(prices, "SPY", of_prices(prices))
    calendar = pd.DatetimeIndex(sorted(prices["date"].unique()))

    model = fit(features, labels, calendar)

    assert model.train_rows > 0
    assert model.holdout_rows > 0
    assert model.holdout_auc is None or 0 <= model.holdout_auc <= 1
    probabilities = model.predict(features.dropna())
    assert probabilities.between(0, 1).all()
    assert probabilities.nunique() > 1


def test_fit_is_repeatable(prices: pd.DataFrame) -> None:
    features = rank_by_date(technical_features(prices, "SPY"))
    labels = excess_return_labels(prices, "SPY", of_prices(prices))
    calendar = pd.DatetimeIndex(sorted(prices["date"].unique()))

    first = fit(features, labels, calendar).predict(features.dropna())
    second = fit(features, labels, calendar).predict(features.dropna())

    pd.testing.assert_series_equal(first, second)


def test_fit_refuses_a_history_too_short_to_split(prices: pd.DataFrame) -> None:
    short = prices[prices["date"] < "2024-06-01"]
    features = rank_by_date(technical_features(short, "SPY"))
    labels = excess_return_labels(short, "SPY", of_prices(short))
    calendar = pd.DatetimeIndex(sorted(short["date"].unique()))

    with pytest.raises(ValueError, match="too few"):
        fit(features, labels, calendar)


def test_decile_scores_give_thirty_stocks_unique_ranks_and_scores_one_to_ten() -> None:
    probabilities = pd.Series(
        np.linspace(0.4, 0.6, 30), index=[f"T{i:02d}" for i in range(30)]
    )

    table = decile_scores(probabilities)

    assert sorted(table["rank"]) == list(range(1, 31))
    assert (
        table["score"].tolist()
        == [10] * 3
        + [9] * 3
        + [8] * 3
        + [7] * 3
        + [6] * 3
        + [5] * 3
        + [4] * 3
        + [3] * 3
        + [2] * 3
        + [1] * 3
    )
    assert table.iloc[0]["ticker"] == "T29"
    assert table.iloc[0]["prob"] == pytest.approx(0.6)


def test_decile_scores_break_ties_by_ticker() -> None:
    table = decile_scores(pd.Series([0.5, 0.5, 0.5], index=["C", "A", "B"]))

    assert table["ticker"].tolist() == ["A", "B", "C"]
    assert table["rank"].tolist() == [1, 2, 3]
    assert set(table["score"]) <= set(range(1, 11))
