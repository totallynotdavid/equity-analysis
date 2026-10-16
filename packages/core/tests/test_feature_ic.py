import numpy as np
import pandas as pd
import pytest

from index_core.evaluation import MIN_NAMES
from index_core.feature_ic import FeatureIC, feature_ics


MONTHS = 36
STOCKS = 30


def _rows(seed: int = 0) -> pd.DataFrame:
    """Two snapshots a month. `roa` ranks the stocks as their excess return
    does, `ret_21` ranks them backwards, and `bb_pctb` is unrelated."""
    rng = np.random.default_rng(seed)
    dates = pd.DatetimeIndex(
        [
            day
            for month in pd.period_range("2020-01", periods=MONTHS, freq="M")
            for day in (month.start_time, month.start_time + pd.Timedelta(days=14))
        ]
    )
    index = pd.MultiIndex.from_product(
        [dates, [f"S{i:02d}" for i in range(STOCKS)]], names=["date", "ticker"]
    )
    excess = pd.Series(rng.standard_normal(len(index)), index=index, name="excess")
    frame = pd.DataFrame({"excess": excess})
    frame["roa"] = excess.groupby(level="date").rank(pct=True)
    frame["ret_21"] = 1 - frame["roa"]
    frame["bb_pctb"] = pd.Series(rng.random(len(index)), index=index)
    return frame


def _by_name(rows: pd.DataFrame) -> dict[str, FeatureIC]:
    return {
        result.feature: result
        for result in feature_ics(rows, ["roa", "ret_21", "bb_pctb"])
    }


def test_a_feature_ranking_like_the_outcome_has_ic_one_and_backwards_minus_one() -> (
    None
):
    results = _by_name(_rows())

    assert results["roa"].mean_ic.value == pytest.approx(1.0)
    assert results["ret_21"].mean_ic.value == pytest.approx(-1.0)


def test_an_unrelated_feature_has_an_interval_around_zero_and_a_small_t() -> None:
    result = _by_name(_rows())["bb_pctb"]

    assert result.mean_ic.low < 0 < result.mean_ic.high
    assert abs(result.t_stat) < 2
    assert result.dates == 2 * MONTHS


def test_features_are_tagged_with_their_family() -> None:
    results = _by_name(_rows())

    assert results["roa"].family == "fundamental"
    assert results["ret_21"].family == "technical"


def test_a_date_with_too_few_values_for_a_feature_is_not_counted_for_it() -> None:
    rows = _rows()
    first = rows.index.get_level_values("date")[0]
    thin = rows.index.get_level_values("date") == first
    keep = np.zeros(len(rows), dtype=bool)
    keep[np.flatnonzero(thin)[: MIN_NAMES - 1]] = True
    rows.loc[thin & ~keep, "roa"] = np.nan

    results = _by_name(rows)

    assert results["roa"].dates == 2 * MONTHS - 1
    assert results["ret_21"].dates == 2 * MONTHS


def test_a_stock_with_no_outcome_is_left_out_of_every_features_correlation() -> None:
    rows = _rows()
    rows.loc[rows.index[::7], "excess"] = np.nan
    rows.loc[rows.index[::5], "bb_pctb"] = np.nan

    results = _by_name(rows)

    assert results["roa"].mean_ic.value == pytest.approx(1.0)
    assert results["ret_21"].mean_ic.value == pytest.approx(-1.0)
    assert results["bb_pctb"].dates == 2 * MONTHS


def test_tied_values_share_their_mean_rank() -> None:
    rows = _rows()
    rows["roa"] = rows["roa"].round(1)
    rows["excess"] = rows["excess"].round(1)

    by_date = [
        group["roa"].corr(group["excess"], method="spearman")
        for _, group in rows.groupby(level="date")
    ]

    assert _by_name(rows)["roa"].mean_ic.value == pytest.approx(np.mean(by_date))
