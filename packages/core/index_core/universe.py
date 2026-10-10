"""A point-in-time universe: which tickers were members on which dates.

A universe file has one membership per line, `ticker,start,end`, and `#` starts
a comment. A name is a member from `start` through the day before `end`, so
`end` is the first day it is not. An empty or missing `end` means it still is.
A ticker can have several memberships if it left and came back.
"""

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd


if TYPE_CHECKING:
    from pathlib import Path

BENCHMARK = "SPY"


@dataclass(frozen=True)
class Membership:
    ticker: str
    start: date
    end: date | None


@dataclass(frozen=True)
class Universe:
    name: str
    memberships: tuple[Membership, ...]

    @property
    def tickers(self) -> list[str]:
        """Every ticker that is ever a member, in file order."""
        return list(dict.fromkeys(m.ticker for m in self.memberships))

    def members_between(
        self, first: date | pd.Timestamp, last: date | pd.Timestamp
    ) -> list[str]:
        """The tickers that are members on at least one day of `first` to `last`."""
        low, high = pd.Timestamp(first), pd.Timestamp(last)
        return list(
            dict.fromkeys(
                m.ticker
                for m in self.memberships
                if pd.Timestamp(m.start) <= high
                and (m.end is None or low < pd.Timestamp(m.end))
            )
        )

    def members_on(self, day: date | pd.Timestamp) -> list[str]:
        """The tickers that are members on `day`, in file order."""
        return self.members_between(day, day)

    def mask(self, index: pd.Index) -> np.ndarray:
        """True for each (`date`, `ticker`) of `index` that is a member row."""
        dates = index.get_level_values("date")
        tickers = index.get_level_values("ticker")
        member = np.zeros(len(index), dtype=bool)
        by_ticker = pd.Series(np.arange(len(index))).groupby(np.asarray(tickers))
        rows = {str(ticker): group.to_numpy() for ticker, group in by_ticker}
        for m in self.memberships:
            positions = rows.get(m.ticker)
            if positions is None:
                continue
            days = dates[positions]
            inside = days >= pd.Timestamp(m.start)
            if m.end is not None:
                inside &= days < pd.Timestamp(m.end)
            member[positions[inside]] = True
        return member


def read_universe(path: Path) -> Universe:
    memberships: list[Membership] = []
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        text = line.split("#", 1)[0].strip()
        if not text:
            continue
        memberships.append(_parse(text, f"{path}:{number}"))
    if not memberships:
        raise ValueError(f"{path} lists no tickers")
    _check_overlaps(memberships, path)
    return Universe(path.stem, tuple(memberships))


def _parse(text: str, where: str) -> Membership:
    fields = [field.strip() for field in text.split(",")]
    if len(fields) not in (2, 3) or not fields[1]:
        raise ValueError(f"{where}: expected `ticker,start[,end]`, got `{text}`")
    ticker = fields[0].upper()
    if ticker == BENCHMARK:
        raise ValueError(f"{where}: {BENCHMARK} is the benchmark, not a member")
    try:
        start = date.fromisoformat(fields[1])
        end = date.fromisoformat(fields[2]) if len(fields) == 3 and fields[2] else None
    except ValueError as error:
        raise ValueError(f"{where}: dates are YYYY-MM-DD: {error}") from error
    if end is not None and end <= start:
        raise ValueError(f"{where}: {ticker} ends on {end}, not after {start}")
    return Membership(ticker, start, end)


def _check_overlaps(memberships: list[Membership], path: Path) -> None:
    last_end: dict[str, date | None] = {}
    for m in sorted(memberships, key=lambda m: (m.ticker, m.start)):
        if m.ticker in last_end:
            end = last_end[m.ticker]
            if end is None or m.start < end:
                raise ValueError(f"{m.ticker} has overlapping memberships in {path}")
        last_end[m.ticker] = m.end
