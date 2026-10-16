"""The daily metrics of a universe and the percentile of each value."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from index_core.features.fundamental import INPUTS
from index_core.metrics import MIN_PEERS, daily_metrics
from index_core.universe import Membership, Universe


DAYS = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])


def _universe(tickers: list[str]) -> Universe:
    return Universe(
        "toy", tuple(Membership(t, date(2000, 1, 1), None) for t in tickers)
    )


def _inputs(**columns: dict[str, list[float]]) -> pd.DataFrame:
    """Inputs for every ticker on `DAYS`: each keyword is an input, mapping a
    ticker to its value on each day. Other inputs are missing."""
    tickers = sorted(next(iter(columns.values())))
    index = pd.MultiIndex.from_product([DAYS, tickers], names=["date", "ticker"])
    frame = pd.DataFrame(np.nan, index=index, columns=list(INPUTS))
    for name, by_ticker in columns.items():
        for ticker, values in by_ticker.items():
            for day, value in zip(DAYS, values, strict=True):
                frame.loc[(day, ticker), name] = value
    return frame


def _row(result: pd.DataFrame, ticker: str, metric: str, day: str) -> pd.Series:
    (row,) = (
        r
        for _, r in result.iterrows()
        if (r["ticker"], r["metric"], r["as_of"]) == (ticker, metric, pd.Timestamp(day))
    )
    return row


def test_the_percentile_is_the_share_of_members_at_or_below_the_value() -> None:
    tickers = list("ABCDEF")
    inputs = _inputs(net_income={t: [i + 1.0] * 3 for i, t in enumerate(tickers)})

    result = daily_metrics(inputs, _universe(tickers), {})

    for i, ticker in enumerate(tickers):
        row = _row(result, ticker, "net_income", "2024-01-04")
        assert row["value"] == i + 1
        assert row["pct_universe"] == pytest.approx(100 * (i + 1) / 6)
        assert np.isnan(row["pct_sector"])


def test_tied_values_count_every_member_at_or_below_them() -> None:
    tickers = list("ABCDE")
    values = dict(zip(tickers, [1.0, 1.0, 2.0, 3.0, 4.0], strict=True))
    inputs = _inputs(net_income={t: [v] * 3 for t, v in values.items()})

    result = daily_metrics(inputs, _universe(tickers), dict.fromkeys(tickers, "S"))

    for ticker, percent in zip(tickers, [40.0, 40.0, 60.0, 80.0, 100.0], strict=True):
        row = _row(result, ticker, "net_income", "2024-01-04")
        assert row["pct_universe"] == percent
        assert row["pct_sector"] == percent


def test_a_sector_percentile_ranks_within_the_sector_and_needs_enough_peers() -> None:
    tickers = list("ABCDEFG")
    inputs = _inputs(net_income={t: [i + 1.0] * 3 for i, t in enumerate(tickers)})
    sectors = {**dict.fromkeys("ABCDE", "Tech"), "F": "Energy", "G": "Energy"}

    result = daily_metrics(inputs, _universe(tickers), sectors)

    assert MIN_PEERS == 5
    assert _row(result, "E", "net_income", "2024-01-04")["pct_sector"] == 100.0
    assert _row(result, "A", "net_income", "2024-01-04")["pct_sector"] == 20.0
    assert np.isnan(_row(result, "G", "net_income", "2024-01-04")["pct_sector"])
    assert _row(result, "G", "net_income", "2024-01-04")["pct_universe"] == 100.0


def test_a_group_under_the_minimum_has_no_percentile() -> None:
    tickers = list("ABCD")
    inputs = _inputs(net_income={t: [i + 1.0] * 3 for i, t in enumerate(tickers)})

    result = daily_metrics(inputs, _universe(tickers), {})

    assert result["pct_universe"].isna().all()
    assert len(result) == 4 * 3


def test_a_ticker_outside_the_universe_is_neither_reported_nor_ranked() -> None:
    tickers = list("ABCDEF")
    inputs = _inputs(net_income={t: [i + 1.0] * 3 for i, t in enumerate(tickers)})

    result = daily_metrics(inputs, _universe(tickers[:5]), {})

    assert set(result["ticker"]) == set("ABCDE")
    assert _row(result, "E", "net_income", "2024-01-04")["pct_universe"] == 100.0


def test_a_ratio_is_missing_where_its_divisor_is_not_positive() -> None:
    tickers = list("ABCDEF")
    revenue = {t: [100.0, 100.0, 100.0] for t in tickers}
    revenue["F"] = [0.0, 0.0, 0.0]
    inputs = _inputs(
        revenue=revenue, net_income={t: [10.0, 10.0, 10.0] for t in tickers}
    )

    result = daily_metrics(inputs, _universe(tickers), {})

    margins = result[result["metric"] == "net_margin"]
    assert set(margins["ticker"]) == set("ABCDE")
    assert (margins["value"] == 0.1).all()
    assert not np.isinf(result["value"]).any()


def test_only_the_latest_days_are_kept() -> None:
    tickers = list("ABCDEF")
    inputs = _inputs(net_income={t: [1.0, 2.0, 3.0] for t in tickers})

    result = daily_metrics(inputs, _universe(tickers), {}, days=2)

    assert sorted(result["as_of"].unique()) == list(DAYS[1:])


def test_a_derived_metric_is_computed_from_the_inputs_of_its_day() -> None:
    tickers = list("ABCDEF")
    inputs = _inputs(
        operating_cash_flow={t: [10.0, 20.0, 30.0] for t in tickers},
        capex={t: [1.0, 2.0, 3.0] for t in tickers},
    )

    result = daily_metrics(inputs, _universe(tickers), {})

    assert [
        _row(result, "A", "free_cash_flow", day)["value"]
        for day in ("2024-01-02", "2024-01-03", "2024-01-04")
    ] == [9.0, 18.0, 27.0]
