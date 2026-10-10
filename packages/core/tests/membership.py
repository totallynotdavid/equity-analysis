"""Universes for tests that do not care about membership dates."""

from datetime import date
from typing import TYPE_CHECKING

from index_core.universe import BENCHMARK, Membership, Universe


if TYPE_CHECKING:
    from collections.abc import Iterable

    import pandas as pd


def always(tickers: Iterable[str], name: str = "test") -> Universe:
    """A universe whose names are members on every date."""
    return Universe(
        name, tuple(Membership(ticker, date(1990, 1, 1), None) for ticker in tickers)
    )


def of_prices(prices: pd.DataFrame) -> Universe:
    """Every ticker of a long price frame except the benchmark, always a member."""
    return always(sorted(set(prices["ticker"]) - {BENCHMARK}))
