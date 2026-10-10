from datetime import date

import pandas as pd

from index_core.labels import LABEL_SPAN, excess_return_labels
from index_core.universe import Membership, Universe
from membership import always


FIRST_DAY = pd.bdate_range("2024-01-01", periods=1)[0]


def _long(closes: dict[str, list[float]]) -> pd.DataFrame:
    days = pd.bdate_range("2024-01-01", periods=len(next(iter(closes.values()))))
    return pd.concat(
        pd.DataFrame({"date": days, "ticker": ticker, "adj_close": values})
        for ticker, values in closes.items()
    )


def _labels(closes: dict[str, list[float]]) -> pd.Series:
    members = always(ticker for ticker in closes if ticker != "SPY")
    return excess_return_labels(_long(closes), "SPY", members)


def test_label_is_one_when_the_stock_beats_the_benchmark_and_zero_otherwise() -> None:
    days = LABEL_SPAN + 3
    labels = _labels(
        {
            "SPY": [100.0 + i for i in range(days)],
            "WIN": [100.0 + 2 * i for i in range(days)],
            "LOSE": [100.0 * 0.99**i for i in range(days)],
        }
    )

    assert labels[(FIRST_DAY, "WIN")] == 1.0
    assert labels[(FIRST_DAY, "LOSE")] == 0.0


def test_a_jump_before_the_entry_close_does_not_count() -> None:
    days = LABEL_SPAN + 3
    market = [100.0 + i for i in range(days)]
    # All of the stock's gain lands between day 0 and day 1. A trade entered at
    # the day-1 close never sees it.
    jumper = [100.0] + [200.0] * (days - 1)

    labels = _labels({"SPY": market, "JUMP": jumper})

    assert labels[(FIRST_DAY, "JUMP")] == 0.0


def test_the_last_64_dates_have_no_label() -> None:
    days = LABEL_SPAN + 5
    labels = _labels({"SPY": [100.0] * days, "AAA": [100.0 + i for i in range(days)]})

    assert len(labels) == days - LABEL_SPAN


def test_a_tie_with_the_benchmark_is_not_a_win() -> None:
    days = LABEL_SPAN + 2
    flat = [100.0] * days

    labels = _labels({"SPY": flat, "AAA": flat})

    assert (labels == 0.0).all()


def test_a_missing_entry_close_leaves_that_date_unlabelled() -> None:
    days = LABEL_SPAN + 2
    gappy = [100.0 + i for i in range(days)]
    gappy[1] = float("nan")

    labels = _labels({"SPY": [100.0 + i for i in range(days)], "AAA": gappy})

    assert (FIRST_DAY, "AAA") not in labels.index


def test_a_stock_is_labelled_only_on_the_dates_it_is_a_member() -> None:
    days = LABEL_SPAN + 20
    closes = {
        "SPY": [100.0 + i for i in range(days)],
        "OUT": [100.0 + 2 * i for i in range(days)],
        "IN": [100.0 + 2 * i for i in range(days)],
    }
    join, leave = FIRST_DAY + pd.offsets.BDay(5), FIRST_DAY + pd.offsets.BDay(12)
    universe = Universe(
        "window",
        (
            Membership("IN", join.date(), leave.date()),
            Membership("OUT", date(1990, 1, 1), join.date()),
        ),
    )

    labels = excess_return_labels(_long(closes), "SPY", universe)

    inside = labels.xs("IN", level="ticker").index
    assert list(inside) == list(pd.bdate_range(join, leave - pd.offsets.BDay(1)))
    outside = labels.xs("OUT", level="ticker").index
    assert list(outside) == list(pd.bdate_range(FIRST_DAY, join - pd.offsets.BDay(1)))


def test_a_removed_stock_keeps_the_labels_whose_window_runs_past_its_removal() -> None:
    days = LABEL_SPAN + 20
    removal = FIRST_DAY + pd.offsets.BDay(10)
    universe = Universe(
        "removed", (Membership("AAA", date(1990, 1, 1), removal.date()),)
    )
    closes = {
        "SPY": [100.0 + i for i in range(days)],
        "AAA": [100.0 + 2 * i for i in range(days)],
    }

    labels = excess_return_labels(_long(closes), "SPY", universe)

    last_member_day = removal - pd.offsets.BDay(1)
    assert labels.index.get_level_values("date").max() == last_member_day
    assert labels[(last_member_day, "AAA")] == 1.0
