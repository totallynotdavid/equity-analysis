"""Tiingo end-of-day prices."""

import os

from typing import TYPE_CHECKING

import httpx2
import pandas as pd

from index_core.sources.base import (
    PRICE_COLUMNS,
    NotFoundError,
    SourceError,
    check_range,
    normalise_ticker,
)
from index_core.sources.transport import CachingTransport, PacedTransport


if TYPE_CHECKING:
    from datetime import date
    from pathlib import Path

BASE_URL = "https://api.tiingo.com"
KEY_VARIABLE = "TIINGO_API_KEY"
# Seconds between requests. A free key allows 50 requests an hour.
MIN_INTERVAL = 3600 / 50

_FIELDS = {
    "close": "close",
    "volume": "volume",
    "adjOpen": "adj_open",
    "adjHigh": "adj_high",
    "adjLow": "adj_low",
    "adjClose": "adj_close",
    "adjVolume": "adj_volume",
    "divCash": "div_cash",
    "splitFactor": "split_factor",
}


class TiingoSource:
    name = "tiingo"

    def __init__(
        self,
        api_key: str,
        transport: httpx2.BaseTransport | None = None,
        min_interval: float = MIN_INTERVAL,
        cache: Path | None = None,
    ) -> None:
        paced: httpx2.BaseTransport = PacedTransport(
            transport or httpx2.HTTPTransport(), min_interval
        )
        if cache is not None:
            paced = CachingTransport(paced, cache)
        self._client = httpx2.Client(
            base_url=BASE_URL,
            headers={"Authorization": f"Token {api_key}"},
            timeout=30.0,
            transport=paced,
        )

    @classmethod
    def from_env(cls, cache: Path | None = None) -> TiingoSource:
        api_key = os.environ.get(KEY_VARIABLE)
        if not api_key:
            raise SourceError(f"{KEY_VARIABLE} is not set")
        return cls(api_key, cache=cache)

    def fetch(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        check_range(start, end)
        try:
            response = self._client.get(
                f"/tiingo/daily/{normalise_ticker(ticker)}/prices",
                params={
                    "startDate": start.isoformat(),
                    "endDate": end.isoformat(),
                    "format": "json",
                },
            )
        except httpx2.HTTPError as error:
            raise SourceError(
                f"could not reach Tiingo for {ticker}: {error!r}"
            ) from error
        if response.status_code == 404:
            raise NotFoundError(f"Tiingo has no ticker {ticker}")
        if response.status_code == 429:
            raise SourceError(
                f"Tiingo refused {ticker}: {response.text[:200]} Responses "
                "already fetched are cached, so run the same command again "
                "once the limit has reset."
            )
        if response.status_code != 200:
            raise SourceError(
                f"Tiingo returned HTTP {response.status_code} for {ticker}: "
                f"{response.text[:200]}"
            )
        try:
            bars = response.json()
        except ValueError as error:
            raise SourceError(f"Tiingo sent invalid JSON for {ticker}") from error
        if not isinstance(bars, list):
            raise SourceError(f"Tiingo sent {bars!r:.200} for {ticker}, not bars")
        if not bars:
            raise NotFoundError(f"Tiingo has no prices for {ticker} in {start}..{end}")

        frame = pd.DataFrame(bars)
        missing = {"date", *_FIELDS} - set(frame.columns)
        if missing:
            raise SourceError(f"Tiingo bars for {ticker} lack {sorted(missing)}")
        dates = pd.to_datetime(frame["date"], utc=True).dt.tz_localize(None)
        frame = frame.rename(columns=_FIELDS)[list(PRICE_COLUMNS)]
        frame.index = pd.DatetimeIndex(dates.dt.normalize(), name="date")
        frame = frame.astype(float).sort_index()
        # Tiingo repeats a delisted company's last close with no volume up to
        # the end of the range. Those bars are not trades.
        traded = frame.index[frame["volume"] > 0]
        if traded.empty:
            raise NotFoundError(f"Tiingo has no trades for {ticker} in {start}..{end}")
        return frame.loc[: traded[-1]]
