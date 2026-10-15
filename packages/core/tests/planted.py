"""Fake prices with a signal planted in them, for tests of what a model can find."""

import numpy as np
import pandas as pd


def planted_prices(
    tickers: int = 30, days: int = 2900, seed: int = 0, signal: float = 1.0
) -> pd.DataFrame:
    """Fake prices in which each stock's drift is persistent and its own.

    A stock's daily drift follows a slow random walk that reverts over about
    200 days, so its past return says something real about its next 63 days. A
    model that reads past returns can rank the stocks; one that cannot scores
    zero. SPY drifts alone and gets none of this. With `signal` 0 no stock has
    a drift of its own, so there is nothing to find.
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2012-01-02", periods=days, name="date")
    names = ["SPY"] + [f"T{i:02d}" for i in range(tickers)]
    drift = np.zeros((days, tickers))
    for day in range(1, days):
        drift[day] = 0.995 * drift[day - 1] + 0.00008 * rng.standard_normal(tickers)
    returns = np.column_stack(
        [
            0.0004 + 0.01 * rng.standard_normal(days),
            signal * drift + 0.015 * rng.standard_normal((days, tickers)),
        ]
    )
    close = 100 * np.exp(np.cumsum(returns, axis=0))
    frames = []
    for column, name in enumerate(names):
        price = close[:, column]
        volume = np.round(2e6 * np.exp(0.3 * rng.standard_normal(days)))
        spread = 1 + 0.004 * np.abs(rng.standard_normal((days, 2)))
        frames.append(
            pd.DataFrame(
                {
                    "date": dates,
                    "ticker": name,
                    "close": price,
                    "volume": volume,
                    "adj_open": np.roll(price, 1),
                    "adj_high": price * spread[:, 0],
                    "adj_low": price / spread[:, 1],
                    "adj_close": price,
                    "adj_volume": volume,
                    "div_cash": 0.0,
                    "split_factor": 1.0,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)
