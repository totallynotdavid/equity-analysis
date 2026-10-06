"""Deterministic fake prices and filings for tests and offline runs.

Every ticker follows one shared market factor plus its own noise, with a fixed
drift, beta and volatility, so features such as beta are meaningful. These are
not market prices: scores built from them mean nothing.

A price depends on its ticker and date only. The series always starts at `EPOCH`
and is cut to the requested range, so fetching a different range never changes
a bar. Filings are the same: a company's quarterly reports run from
`FIRST_YEAR` to `LAST_YEAR`, and a request only cuts them at its end date.
"""

import zlib

from datetime import date
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from index_core.sources.base import SourceError, check_range
from index_core.sources.edgar import CONCEPTS, parse_companyfacts


if TYPE_CHECKING:
    from collections.abc import Sequence

EPOCH = date(2000, 1, 3)
TRADING_DAYS = 252
FIRST_YEAR, LAST_YEAR = 2000, 2030

# Stream ids keep the draws of one kind independent of the others.
_MARKET, _RETURNS, _SHAPE, _VOLUME, _FILINGS = range(5)

# What each concept is as a share of quarterly revenue (flows) or of annual
# revenue (balance sheet), before a company's own margin and noise.
_FLOW_SHARE = {
    "revenue": 1.0,
    "gross_profit": 0.45,
    "operating_income": 0.15,
    "net_income": 0.10,
    "depreciation": 0.04,
    "operating_cash_flow": 0.13,
    "capex": 0.05,
}
_BALANCE_SHARE = {
    "total_assets": 1.8,
    "long_term_debt": 0.5,
    "current_debt": 0.1,
    "equity": 0.7,
    "cash": 0.2,
}


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


class SyntheticFilings:
    """Quarterly and annual reports of calendar-year companies, as EDGAR would
    send them. They go through `parse_companyfacts`, so the parser is exercised
    on the same path a real company takes."""

    name = "synthetic"

    def __init__(self, seed: int = 0) -> None:
        self._seed = seed

    def facts(self, ticker: str, end: date) -> pd.DataFrame:
        frame = parse_companyfacts(self.companyfacts(ticker), ticker)
        return frame[frame["filed"] <= pd.Timestamp(end)].reset_index(drop=True)

    def companyfacts(self, ticker: str) -> dict[str, Any]:
        quarters = 4 * (LAST_YEAR - FIRST_YEAR + 1)
        rng = np.random.default_rng([self._seed, _FILINGS, _crc(ticker)])
        growth = 0.04 * _unit(ticker, "growth") - 0.005
        revenue = (
            2.5e8
            * np.exp(3 * _unit(ticker, "size"))
            * np.exp(np.cumsum(growth + 0.03 * rng.standard_normal(quarters)))
        )
        flows = {
            name: revenue
            * share
            * (0.5 + _unit(ticker, name))
            * np.exp(0.05 * rng.standard_normal(quarters))
            for name, share in _FLOW_SHARE.items()
        }
        flows["revenue"] = revenue
        balance = {
            name: 4
            * revenue
            * share
            * (0.5 + _unit(ticker, name))
            * np.exp(0.02 * rng.standard_normal(quarters))
            for name, share in _BALANCE_SHARE.items()
        }
        balance["shares"] = (
            1e8
            * np.exp(2 * _unit(ticker, "shares"))
            * np.exp(np.cumsum(0.002 * rng.standard_normal(quarters)))
        )

        facts: dict[str, dict[str, dict[str, Any]]] = {"dei": {}, "us-gaap": {}}
        units: dict[str, list[dict[str, Any]]] = {name: [] for name in CONCEPTS}
        for position in range(quarters):
            year, quarter = FIRST_YEAR + position // 4, position % 4 + 1
            period_end = pd.Timestamp(year, 3 * quarter, 1) + pd.offsets.MonthEnd(0)
            filed = (
                period_end + pd.Timedelta(days=55 if quarter == 4 else 40)
            ) + pd.offsets.BDay(0)
            filing = {
                "accn": f"{_crc(ticker):010d}-{year % 100:02d}-{quarter:06d}",
                "fy": year,
                "fp": "FY" if quarter == 4 else f"Q{quarter}",
                "form": "10-K" if quarter == 4 else "10-Q",
                "filed": filed.date().isoformat(),
            }
            year_start = position - quarter + 1
            for name, series in flows.items():
                units[name].append(
                    {
                        "start": f"{year}-01-01",
                        "end": period_end.date().isoformat(),
                        "val": float(series[year_start : position + 1].sum()),
                        **filing,
                    }
                )
            for name, series in balance.items():
                end_date = (
                    period_end if name != "shares" else filed - pd.Timedelta(days=8)
                )
                units[name].append(
                    {
                        "end": end_date.date().isoformat(),
                        "val": float(round(series[position])),
                        **filing,
                    }
                )
        for name, entries in units.items():
            concept = CONCEPTS[name]
            facts[concept.taxonomy][concept.tags[0]] = {
                "units": {concept.unit: entries}
            }
        return {"cik": _crc(ticker), "entityName": ticker, "facts": facts}
