from datetime import date
from typing import TYPE_CHECKING

import pytest

from index_core.backtest_text import render
from index_core.pipeline import Backtest, backtest, history_needed
from index_core.sources.base import PRICE_COLUMNS
from index_core.store import Store
from membership import always


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import pandas as pd

TICKERS = [f"T{i:02d}" for i in range(14)]
UNIVERSE = always(TICKERS, "planted")
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
    development = backtest(store, UNIVERSE, FIRST_OOS, holdout_months=12)
    holdout = backtest(store, UNIVERSE, FIRST_OOS, holdout_months=12, final=True)

    assert development.period == "development"
    assert holdout.period == "holdout"
    assert development.holdout_start == holdout.holdout_start
    cutoff = development.holdout_start
    assert development.evaluation.last_date <= cutoff
    assert holdout.evaluation.first_date > cutoff
    assert development.evaluation.first_date >= FIRST_OOS
    assert development.price_source == "synthetic"


def test_a_ticker_without_stored_prices_is_left_out_and_named(store: Store) -> None:
    with_gap = always([*TICKERS, "NOPE"], "planted")

    full = backtest(store, UNIVERSE, FIRST_OOS, holdout_months=12)
    gapped = backtest(store, with_gap, FIRST_OOS, holdout_months=12)

    assert gapped.unpriced == ["NOPE"]
    assert full.unpriced == []
    assert set(gapped.predictions.index.get_level_values("ticker")) == set(TICKERS)
    text = render(gapped, "planted")
    assert (
        f"Missing: no prices for 1 of {len(TICKERS) + 1} universe names (NOPE)" in text
    )
    assert "Missing" not in render(full, "planted")


def test_a_holdout_longer_than_the_history_stops_the_backtest(store: Store) -> None:
    with pytest.raises(ValueError, match="needs at least"):
        backtest(store, UNIVERSE, FIRST_OOS, holdout_months=1000)


def test_the_text_shows_the_base_rate_the_effective_sample_and_ten_score_rows(
    store: Store,
) -> None:
    result: Backtest = backtest(store, UNIVERSE, FIRST_OOS, holdout_months=12)

    text = render(result, "planted")

    evaluation = result.evaluation
    assert "synthetic prices" in text
    assert f"Base rate, share of stocks beating SPY: {evaluation.base_rate:.1%}" in text
    assert f"about {evaluation.independent_windows} independent 63-day windows" in text
    assert "Newey-West" in text
    lines = text.splitlines()
    table = lines[lines.index(next(x for x in lines if x.startswith("score "))) + 1 :]
    assert [int(line.split()[0]) for line in table[:10]] == list(range(10, 0, -1))


def _store_with_days(path: Path, planted: pd.DataFrame, days: int) -> Store:
    frame = planted[planted["ticker"].isin(["SPY", *TICKERS])]
    first_days = sorted(frame["date"].unique())[:days]
    frame = frame[frame["date"].isin(first_days)]
    store = Store.open(path)
    for ticker, bars in frame.groupby("ticker"):
        store.upsert_prices(
            "synthetic", str(ticker), bars.set_index("date")[list(PRICE_COLUMNS)]
        )
    return store


@pytest.mark.parametrize("final", [False, True])
def test_a_history_below_the_stated_need_is_refused_with_the_start_to_fetch(
    tmp_path: Path, planted: pd.DataFrame, final: bool
) -> None:
    needed = history_needed(12, final=final)

    with (
        _store_with_days(tmp_path / "db.sqlite", planted, needed - 1) as store,
        pytest.raises(ValueError, match=rf"needs at least {needed}") as error,
    ):
        backtest(store, UNIVERSE, holdout_months=12, final=final)

    assert "Run `eq run --start 20" in str(error.value)


@pytest.mark.parametrize("final", [False, True])
def test_a_history_a_quarter_above_the_stated_need_backtests(
    tmp_path: Path, planted: pd.DataFrame, final: bool
) -> None:
    with _store_with_days(
        tmp_path / "db.sqlite", planted, history_needed(12, final=final) + 126
    ) as store:
        result = backtest(store, UNIVERSE, holdout_months=12, final=final)

    assert result.folds
