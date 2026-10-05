import pandas as pd

from index_core.labels import LABEL_SPAN, excess_return_labels


FIRST_DAY = pd.bdate_range("2024-01-01", periods=1)[0]


def _long(closes: dict[str, list[float]]) -> pd.DataFrame:
    days = pd.bdate_range("2024-01-01", periods=len(next(iter(closes.values()))))
    return pd.concat(
        pd.DataFrame({"date": days, "ticker": ticker, "adj_close": values})
        for ticker, values in closes.items()
    )


def test_label_is_one_when_the_stock_beats_the_benchmark_and_zero_otherwise() -> None:
    days = LABEL_SPAN + 3
    labels = excess_return_labels(
        _long(
            {
                "SPY": [100.0 + i for i in range(days)],
                "WIN": [100.0 + 2 * i for i in range(days)],
                "LOSE": [100.0 * 0.99**i for i in range(days)],
            }
        ),
        "SPY",
    )

    assert labels[(FIRST_DAY, "WIN")] == 1.0
    assert labels[(FIRST_DAY, "LOSE")] == 0.0


def test_a_jump_before_the_entry_close_does_not_count() -> None:
    days = LABEL_SPAN + 3
    market = [100.0 + i for i in range(days)]
    # All of the stock's gain lands between day 0 and day 1. A trade entered at
    # the day-1 close never sees it.
    jumper = [100.0] + [200.0] * (days - 1)

    labels = excess_return_labels(_long({"SPY": market, "JUMP": jumper}), "SPY")

    assert labels[(FIRST_DAY, "JUMP")] == 0.0


def test_the_last_64_dates_have_no_label() -> None:
    days = LABEL_SPAN + 5
    labels = excess_return_labels(
        _long({"SPY": [100.0] * days, "AAA": [100.0 + i for i in range(days)]}), "SPY"
    )

    assert len(labels) == days - LABEL_SPAN


def test_a_tie_with_the_benchmark_is_not_a_win() -> None:
    days = LABEL_SPAN + 2
    flat = [100.0] * days

    labels = excess_return_labels(_long({"SPY": flat, "AAA": flat}), "SPY")

    assert (labels == 0.0).all()


def test_a_missing_entry_close_leaves_that_date_unlabelled() -> None:
    days = LABEL_SPAN + 2
    gappy = [100.0 + i for i in range(days)]
    gappy[1] = float("nan")

    labels = excess_return_labels(
        _long({"SPY": [100.0 + i for i in range(days)], "AAA": gappy}), "SPY"
    )

    assert (FIRST_DAY, "AAA") not in labels.index
