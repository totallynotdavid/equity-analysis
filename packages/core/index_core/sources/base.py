"""The price-source interface. Network I/O lives behind it and nowhere else."""

from typing import TYPE_CHECKING, Protocol


if TYPE_CHECKING:
    from datetime import date

    import pandas as pd


# `close` and `volume` are as traded. The `adj_` columns are adjusted for splits
# and dividends, except `adj_volume`, which is adjusted for splits only.
PRICE_COLUMNS = (
    "close",
    "volume",
    "adj_open",
    "adj_high",
    "adj_low",
    "adj_close",
    "adj_volume",
)


class SourceError(Exception):
    """A price source could not deliver what was asked of it."""


class PriceSource(Protocol):
    name: str

    def fetch(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """Daily bars from `start` to `end` inclusive.

        The frame has a sorted, unique, tz-naive `DatetimeIndex` named `date` and
        the columns in `PRICE_COLUMNS`. A ticker with no bars in the range is a
        `SourceError`.
        """
        ...
