import numpy as np
import pandas as pd
import pytest

from index_core.evaluation import MIN_NAMES, evaluate, newey_west_t
from index_core.labels import HORIZON


def _predictions(
    dates: pd.DatetimeIndex, sign: float = 1.0, names: int = 10
) -> pd.DataFrame:
    """Per date, stock k has excess return (k - 4.5) / 100 and prob 0.5 + sign * k / 100.

    With `sign` 1 the model ranks perfectly; with -1 it ranks backwards.
    """
    rows = [
        {
            "date": date,
            "ticker": f"T{k:02d}",
            "prob": 0.5 + sign * k / 100,
            "excess": (k - 4.5) / 100,
        }
        for date in dates
        for k in range(names)
    ]
    frame = pd.DataFrame(rows).set_index(["date", "ticker"])
    frame["label"] = (frame["excess"] > 0).astype(float)
    return frame


CALENDAR = pd.bdate_range("2020-01-01", periods=400)
# Two snapshots a month for twelve months.
DATES = pd.DatetimeIndex([CALENDAR[i] for i in range(0, 260, 10)])


def test_a_perfect_ranking_has_ic_one_and_every_top_name_beats_the_benchmark() -> None:
    evaluation = evaluate(_predictions(DATES), CALENDAR)

    assert evaluation.mean_ic.value == pytest.approx(1.0)
    assert evaluation.mean_auc.value == pytest.approx(1.0)
    assert evaluation.base_rate == pytest.approx(0.5)
    # One stock per score. Score 10 is stock 9 with +4.5%, score 1 is -4.5%.
    assert evaluation.spread.value == pytest.approx(0.09)
    assert evaluation.top_hit_rate.value == pytest.approx(1.0)
    ten, one = evaluation.by_score[0], evaluation.by_score[-1]
    assert (ten.score, one.score) == (10, 1)
    assert ten.hit_rate.value == 1.0
    assert one.hit_rate.value == 0.0
    assert ten.mean_excess.value == pytest.approx(0.045)
    assert ten.rows == one.rows == len(DATES)


def test_a_backwards_ranking_has_ic_minus_one_and_a_negative_spread() -> None:
    evaluation = evaluate(_predictions(DATES, sign=-1.0), CALENDAR)

    assert evaluation.mean_ic.value == pytest.approx(-1.0)
    assert evaluation.spread.value == pytest.approx(-0.09)
    assert evaluation.top_hit_rate.value == pytest.approx(0.0)


def test_the_count_of_independent_windows_comes_from_the_span_not_the_rows() -> None:
    evaluation = evaluate(_predictions(DATES), CALENDAR)

    span = CALENDAR.get_loc(DATES[-1]) - CALENDAR.get_loc(DATES[0]) + 1
    assert evaluation.independent_windows == span // HORIZON
    assert evaluation.independent_windows < evaluation.dates
    assert evaluation.first_date == DATES[0].date()
    assert evaluation.last_date == DATES[-1].date()
    assert evaluation.rows == 10 * len(DATES)
    assert evaluation.months == len({(d.year, d.month) for d in DATES})


def test_an_interval_widens_when_months_disagree() -> None:
    steady = _predictions(DATES)
    # Even months rank backwards: the same model, a split verdict.
    flipped = steady.copy()
    months = flipped.index.get_level_values("date").month
    flipped.loc[months % 2 == 0, "prob"] = 1.0 - flipped.loc[months % 2 == 0, "prob"]

    mixed = evaluate(flipped, CALENDAR)

    assert mixed.mean_ic.value == pytest.approx(0.0, abs=0.2)
    assert mixed.mean_ic.low < mixed.mean_ic.value < mixed.mean_ic.high
    assert mixed.mean_ic.high - mixed.mean_ic.low > 0.5


def test_dates_with_too_few_stocks_are_left_out() -> None:
    thin = _predictions(DATES[:3], names=MIN_NAMES - 1)
    full = _predictions(DATES[3:])

    evaluation = evaluate(pd.concat([thin, full]), CALENDAR)

    assert evaluation.dates == len(DATES) - 3
    assert evaluation.first_date == DATES[3].date()


def test_nothing_to_rank_is_refused() -> None:
    with pytest.raises(ValueError, match="stocks to rank"):
        evaluate(_predictions(DATES, names=MIN_NAMES - 1), CALENDAR)


def test_newey_west_equals_the_plain_t_statistic_without_lags() -> None:
    series = np.array([0.3, -0.1, 0.4, 0.2, -0.2, 0.5, 0.1, 0.0])

    plain = series.mean() / (series.std() / np.sqrt(len(series)))

    assert newey_west_t(series, 0) == pytest.approx(plain)


def test_newey_west_shrinks_the_t_statistic_of_a_positively_autocorrelated_series() -> (
    None
):
    rng = np.random.default_rng(0)
    series = np.empty(400)
    series[0] = 0.0
    for i in range(1, 400):
        series[i] = 0.9 * series[i - 1] + rng.standard_normal()
    series += 0.5

    assert newey_west_t(series, 13) < 0.6 * newey_west_t(series, 0)
