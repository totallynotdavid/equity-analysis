"""Loads the quotes table as a daily panel and derives returns from it.

BVL quotes carry no split or dividend factors. A return is the close against
the quote's own previous close (`prev_close`), which spans the days a thin
stock does not trade. A move beyond `MAX_ABS_LOG_RETURN` is taken for an
unrecorded split or a data error and dropped; a stock dividend smaller than
that stays in the data as a loss on its ex date.
"""

import re

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from bvl import store


if TYPE_CHECKING:
    from pathlib import Path

MAX_ABS_LOG_RETURN = float(np.log(2))
# Local BVL tickers end in a share class letter and a series digit (ALICORC1,
# BACKUSI1). Foreign listings such as AMD or BAP carry their home ticker.
_LOCAL_TICKER = re.compile(r"^[A-Z]{3,}[A-Z]\d$")
# Funds and ETFs that fit that pattern (FICORPS2, COWZ1) say so in the name.
_FUND_NAME = re.compile(r"fondo|\betf|ucits", re.IGNORECASE)


def load_quotes(db: Path) -> pd.DataFrame:
    quotes = store.read(
        db,
        "SELECT date, ticker, name, prev_date, prev_close, close, amount FROM quotes "
        "WHERE close > 0",
    )
    quotes["date"] = pd.to_datetime(quotes["date"])
    quotes["prev_date"] = pd.to_datetime(quotes["prev_date"])
    quotes["log_return"] = np.log(quotes["close"] / quotes["prev_close"])
    implausible = quotes["log_return"].abs() > MAX_ABS_LOG_RETURN
    quotes.loc[implausible | ~np.isfinite(quotes["log_return"]), "log_return"] = np.nan
    return quotes.sort_values(["ticker", "date"], ignore_index=True)


def is_local_share(ticker: str, name: str) -> bool:
    """A Peruvian share, not a foreign listing, an investment fund or an ETF."""
    return _LOCAL_TICKER.match(ticker) is not None and _FUND_NAME.search(name) is None


def market_days(quotes: pd.DataFrame) -> pd.DatetimeIndex:
    """Every day on which any stock traded."""
    return pd.DatetimeIndex(sorted(quotes["date"].unique()))


def market_log_level(quotes: pd.DataFrame) -> pd.Series:
    """Log level of an equal-weighted market of `quotes`, on every market day.

    A day's market return is the mean log return of the stocks that also
    traded on the market day before, so a thin stock's return over a gap of
    weeks does not land on one day. The level starts at 0.
    """
    days = market_days(quotes)
    previous = pd.Series(days[:-1], index=days[1:])
    consecutive = (
        quotes["prev_date"].to_numpy() == quotes["date"].map(previous).to_numpy()
    )
    daily: pd.Series = quotes[consecutive].groupby("date")["log_return"].mean()
    level: pd.Series = daily.reindex(days).fillna(0.0).cumsum()
    return level.rename("log_market")
