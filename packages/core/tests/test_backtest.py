from datetime import date
from typing import TYPE_CHECKING

import pytest

from index_core.backtest_text import render
from index_core.pipeline import Backtest, backtest
from index_core.sources.base import PRICE_COLUMNS
from index_core.store import Store


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import pandas as pd

TICKERS = [f"T{i:02d}" for i in range(14)]
FIRST_OOS = date(2019, 1, 1)


@pytest.fixture
def store(tmp_path: Path, planted: pd.DataFrame) -> Iterator[Store]:
    with Store.open(tmp_path / "db.sqlite") as opened:
        for ticker, bars in planted[planted["ticker"].isin(["SPY", *TICKERS])].groupby(
            "ticker"
        ):
            opened.upsert_prices(
                "synthetic",
                str(ticker),
                bars.set_index("date")[list(PRICE_COLUMNS)],
            )
        yield opened


def test_the_holdout_months_are_left_out_until_asked_for(store: Store) -> None:
    development = backtest(store, TICKERS, FIRST_OOS, holdout_months=12)
    holdout = backtest(store, TICKERS, FIRST_OOS, holdout_months=12, final=True)

    assert development.period == "development"
    assert holdout.period == "holdout"
    assert development.holdout_start == holdout.holdout_start
    cutoff = development.holdout_start
    assert development.evaluation.last_date <= cutoff
    assert holdout.evaluation.first_date > cutoff
    assert development.evaluation.first_date >= FIRST_OOS
    assert development.price_source == "synthetic"


def test_a_ticker_without_stored_prices_stops_the_backtest(store: Store) -> None:
    with pytest.raises(ValueError, match=r"no stored prices for \['NOPE'\]"):
        backtest(store, [*TICKERS, "NOPE"], FIRST_OOS)


def test_a_holdout_longer_than_the_history_stops_the_backtest(store: Store) -> None:
    with pytest.raises(ValueError, match="too short"):
        backtest(store, TICKERS, FIRST_OOS, holdout_months=1000)


def test_the_text_shows_the_base_rate_the_effective_sample_and_ten_score_rows(
    store: Store,
) -> None:
    result: Backtest = backtest(store, TICKERS, FIRST_OOS, holdout_months=12)

    text = render(result, "planted")

    evaluation = result.evaluation
    assert "synthetic prices" in text
    assert f"Base rate, share of stocks beating SPY: {evaluation.base_rate:.1%}" in text
    assert f"about {evaluation.independent_windows} independent 63-day windows" in text
    assert "Newey-West" in text
    lines = text.splitlines()
    table = lines[lines.index(next(x for x in lines if x.startswith("score "))) + 1 :]
    assert [int(line.split()[0]) for line in table[:10]] == list(range(10, 0, -1))
