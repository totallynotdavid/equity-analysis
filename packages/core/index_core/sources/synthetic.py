"""Deterministic fake prices for tests and offline runs.

Every ticker follows one shared market factor plus its own noise, with a fixed
drift, beta and volatility, so features such as beta are meaningful. These are
not market prices: scores built from them mean nothing.

A price depends on its ticker and date only. The series always starts at `EPOCH`
and is cut to the requested range, so fetching a different range never changes
a bar.
"""

import zlib

from datetime import date
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from index_core.sources.base import SourceError, check_range


if TYPE_CHECKING:
    from collections.abc import Sequence

EPOCH = date(2000, 1, 3)
TRADING_DAYS = 252

# Stream ids keep the draws of one kind independent of the others.
_MARKET, _RETURNS, _SHAPE, _VOLUME = range(4)


class SyntheticSource:
    name = "synthetic"

    def __init__(self, seed: int = 0) -> None:
        self._seed = seed

    def fetch(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        check_range(start, end)
        if start < EPOCH:
            raise SourceError(f"synthetic prices start at {EPOCH}, not {start}")
        dates = pd.bdate_range(EPOCH, end, name="date")
        n = len(dates)
        market = self._normal([_MARKET], n)
        noise = self._normal([_RETURNS, _crc(ticker)], n, columns=4)

        beta = 0.6 + 0.8 * _unit(ticker, "beta")
        daily_drift = (_unit(ticker, "drift") * 0.2 - 0.02) / TRADING_DAYS
        daily_vol = 0.15 + 0.2 * _unit(ticker, "vol")
        returns = daily_drift + (
            0.16 * beta * market + daily_vol * noise[:, 0]
        ) / np.sqrt(TRADING_DAYS)

        close = 100.0 * np.exp(np.cumsum(returns))
        previous = np.concatenate([[100.0], close[:-1]])
        gap = 0.004 * noise[:, 1]
        adj_open = previous * np.exp(gap)
        spread = 0.006 * np.abs(noise[:, 2:4])
        adj_high = np.maximum(adj_open, close) * np.exp(spread[:, 0])
        adj_low = np.minimum(adj_open, close) * np.exp(-spread[:, 1])
        volume = np.round(2e6 * np.exp(0.3 * self._normal([_VOLUME, _crc(ticker)], n)))

        bars = pd.DataFrame(
            {
                "close": close,
                "volume": volume,
                "adj_open": adj_open,
                "adj_high": adj_high,
                "adj_low": adj_low,
                "adj_close": close,
                "adj_volume": volume,
            },
            index=dates,
        )
        return bars.loc[pd.Timestamp(start) :]

    def _normal(
        self, stream: Sequence[int], n: int, columns: int | None = None
    ) -> np.ndarray:
        """Draws that depend on the position from `EPOCH`, never on `n`."""
        size = n if columns is None else (n, columns)
        return np.random.default_rng([self._seed, *stream]).standard_normal(size)


def _crc(ticker: str) -> int:
    return zlib.crc32(ticker.encode())


def _unit(ticker: str, salt: str) -> float:
    """A stable number in [0, 1) for a ticker."""
    return zlib.crc32(f"{salt}:{ticker}".encode()) / 2**32
