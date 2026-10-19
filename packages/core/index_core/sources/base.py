"""The source interfaces. Network I/O lives behind them and nowhere else."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import pandas as pd


if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date


# `close` and `volume` are as traded. The `adj_` columns are adjusted for splits
# and dividends, except `adj_volume`, which is adjusted for splits only.
# `div_cash` is the dividend per share with its ex-date as the day. `split_factor`
# is the number of new shares per old share from its ex-date on: 4 for a 4-for-1
# split, 0.1 for a 1-for-10 reverse split, and 1 on every other day.
PRICE_COLUMNS = (
    "close",
    "volume",
    "adj_open",
    "adj_high",
    "adj_low",
    "adj_close",
    "adj_volume",
    "div_cash",
    "split_factor",
)


class SourceError(Exception):
    """A price source could not deliver what was asked of it."""


class NotFoundError(SourceError):
    """The source has no such ticker, or none in the range. Other tickers may
    still be fetched."""


def normalise_ticker(ticker: str) -> str:
    """The form both Tiingo and EDGAR list a ticker under: upper case, with a
    dash for the dot of a share class, so `BRK.B` is `BRK-B`."""
    return ticker.strip().upper().replace(".", "-")


def check_range(start: date, end: date) -> None:
    if start > end:
        raise SourceError(f"start {start} is after end {end}")


# Store one row per fact per filing. `concept` is a key of `edgar.CONCEPTS`.
# `start` is NaT for a balance-sheet value. `filed` is the day the filing
# reached the SEC. Nothing in it is known before then. `priority` is the
# position of the tag that reported the value in its concept's list.
# `accn`, `form`, `fy` and `fp` name the filing that reported the value: its
# accession number, its form, and the fiscal year and period it describes.
# `cik` is the registrant that filed it, which differs between the filings of a
# company that moved to a new registrant.
FACT_COLUMNS = (
    "concept",
    "start",
    "end",
    "filed",
    "value",
    "priority",
    "accn",
    "form",
    "fy",
    "fp",
    "cik",
)


def empty_facts() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "concept": pd.Series(dtype=str),
            "start": pd.Series(dtype="datetime64[ns]"),
            "end": pd.Series(dtype="datetime64[ns]"),
            "filed": pd.Series(dtype="datetime64[ns]"),
            "value": pd.Series(dtype=float),
            "priority": pd.Series(dtype=int),
            "accn": pd.Series(dtype=str),
            "form": pd.Series(dtype=str),
            "fy": pd.Series(dtype=int),
            "fp": pd.Series(dtype=str),
            "cik": pd.Series(dtype=int),
        }
    )


@dataclass(frozen=True)
class Security:
    """Who a ticker is, as the filings source names it. `sic` is the SEC's
    Standard Industrial Classification code and `sic_description` its title."""

    cik: int
    name: str
    sic: str
    sic_description: str


class FilingsSource(Protocol):
    name: str

    def facts(self, ticker: str, end: date) -> pd.DataFrame:
        """Every fact of `ticker` filed on or before `end`, as `FACT_COLUMNS`.

        A ticker the source has no company or facts for is a `NotFoundError`,
        which `ingest_filings` treats as no filings. Any other `SourceError`
        stops the run.
        """
        ...

    def security(self, ticker: str) -> Security:
        """The company behind `ticker`. A ticker with no company is a
        `NotFoundError`; any other failure is a `SourceError`."""
        ...


# Factor returns use fractions, not percentages.
# `rf` is the one-day risk-free rate. `mkt_rf` is the market return above it.
FACTOR_COLUMNS = ("mkt_rf", "smb", "hml", "rmw", "cma", "rf")


class FactorSource(Protocol):
    name: str

    def fetch(self) -> pd.DataFrame:
        """Every daily factor return the source has.

        The frame has a sorted, unique, tz-naive `DatetimeIndex` named `date` and
        the columns in `FACTOR_COLUMNS`. Any failure is a `SourceError`.
        """
        ...


class PriceSource(Protocol):
    name: str

    def fetch(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """Daily bars from `start` to `end` inclusive.

        The frame has a sorted, unique, tz-naive `DatetimeIndex` named `date` and
        the columns in `PRICE_COLUMNS`. A ticker the source does not list, or
        with no bars in the range, is a `NotFoundError`, which `ingest` treats as
        no prices unless it is the benchmark. A `start` after `end`, or any
        other failure, is a `SourceError` and stops the run.
        """
        ...


@dataclass(frozen=True)
class Listing:
    """One security that traded under a ticker, from `start` to `end`. A date
    the source leaves out is None."""

    start: date | None
    end: date | None


@runtime_checkable
class ListedSource(PriceSource, Protocol):
    """A price source that also says who traded under each ticker and when."""

    def listings(self) -> Mapping[str, list[Listing]]:
        """Every stock listing by normalised ticker. A ticker with several was
        reused. Any failure is a `SourceError`."""
        ...
