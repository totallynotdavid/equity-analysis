from datetime import date
from typing import NamedTuple

import numpy as np
import pandas as pd
import pytest

from index_core.evaluation import Evaluation, evaluate
from index_core.features.fundamental import fundamental_features, fundamental_inputs
from index_core.features.normalize import rank_by_date
from index_core.labels import excess_returns
from index_core.sources.synthetic import SyntheticFilings
from index_core.walkforward import walk_forward


TICKERS = [f"T{i:02d}" for i in range(30)]
DAYS = 2900
PLANTED_DRIFT = 0.0006
# The valuation ratios divide by a price that carries the planted drift, so
# they could find it without any filing. These are left out.
PRICED = ("sales_to_ev", "ebitda_to_ev", "earnings_yield", "fcf_yield")


class Market(NamedTuple):
    prices: pd.DataFrame
    inputs: pd.DataFrame
    calendar: pd.DatetimeIndex


def _prices(drift: pd.DataFrame, seed: int) -> pd.DataFrame:
    """SPY and one stock per column of `drift`, a daily drift for each date."""
    rng = np.random.default_rng(seed)
    dates = drift.index
    daily = np.column_stack([np.full(len(dates), 0.0004), drift.to_numpy()])
    returns = daily + 0.01 * rng.standard_normal(daily.shape)
    close = 100 * np.exp(np.cumsum(returns, axis=0))
    frames = [
        pd.DataFrame(
            {
                "date": dates,
                "ticker": name,
                "close": close[:, column],
                "volume": 1e6,
                "adj_open": close[:, column],
                "adj_high": close[:, column],
                "adj_low": close[:, column],
                "adj_close": close[:, column],
                "adj_volume": 1e6,
                "div_cash": 0.0,
                "split_factor": 1.0,
            }
        )
        for column, name in enumerate(["SPY", *drift.columns])
    ]
    return pd.concat(frames, ignore_index=True)


def _market(seed: int = 0) -> Market:
    """Thirty companies whose stock drifts with the net margin their latest
    filings show.

    Each fiscal year a company's profit is scaled by a new random factor, so
    which company looks profitable keeps changing and only a model that reads
    the filings in force on each date can rank them. The drift of a day follows
    the margin known that day.
    """
    rng = np.random.default_rng(seed)
    filings = SyntheticFilings()
    facts = pd.concat(
        [filings.facts(t, date(2030, 1, 1)).assign(ticker=t) for t in TICKERS],
        ignore_index=True,
    )
    year = facts["end"].dt.year
    factor = pd.Series(
        np.exp(rng.standard_normal(len(TICKERS) * 40)),
        index=pd.MultiIndex.from_product([TICKERS, range(1990, 2030)]),
    )
    pairs = pd.MultiIndex.from_arrays([facts["ticker"], year])
    profit = facts["concept"] == "net_income"
    facts.loc[profit, "value"] *= factor.reindex(pairs[profit]).to_numpy()

    calendar = pd.bdate_range("2012-01-02", periods=DAYS, name="date")
    inputs = fundamental_inputs(facts, calendar)
    margin = (inputs["net_income"] / inputs["revenue"]).unstack("ticker")
    margin = margin.reindex(columns=TICKERS)
    z = margin.sub(margin.mean(axis=1), axis=0).div(margin.std(axis=1), axis=0)
    drift = (PLANTED_DRIFT * z).fillna(0.0)
    return Market(_prices(drift, seed), inputs, pd.DatetimeIndex(calendar))


@pytest.fixture(scope="module")
def market() -> Market:
    return _market()


def _evaluation(market: Market, inputs: pd.DataFrame) -> Evaluation:
    features = fundamental_features(inputs, market.prices, "SPY")
    features = rank_by_date(features.drop(columns=list(PRICED)))
    excess = excess_returns(market.prices, "SPY")
    result = walk_forward(features, excess, market.calendar)
    return evaluate(result.predictions, market.calendar)


def test_a_signal_planted_in_the_filings_is_found_out_of_sample(
    market: Market,
) -> None:
    evaluation = _evaluation(market, market.inputs)

    assert evaluation.mean_ic.low > 0.03
    assert evaluation.ic_t_stat > 2


def test_filings_dealt_to_other_companies_carry_no_signal(market: Market) -> None:
    # The same filings, handed to another ticker with a new draw every calendar
    # year: their distribution is kept and their link to the company's own stock
    # is cut. A draw that never changed would let the model learn which ticker
    # drifts.
    rng = np.random.default_rng(5)
    draws = {
        year: dict(zip(TICKERS, rng.permutation(TICKERS), strict=True))
        for year in range(1990, 2031)
    }
    dates = market.inputs.index.get_level_values("date")
    names = [
        draws[day.year][ticker]
        for day, ticker in zip(
            dates, market.inputs.index.get_level_values("ticker"), strict=True
        )
    ]
    dealt = market.inputs.set_axis(
        pd.MultiIndex.from_arrays([dates, names], names=["date", "ticker"])
    ).sort_index()

    evaluation = _evaluation(market, dealt)

    assert abs(evaluation.ic_t_stat) < 2
    assert evaluation.mean_ic.low < 0 < evaluation.mean_ic.high
