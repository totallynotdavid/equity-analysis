"""Tiingo end-of-day prices."""

import os

from typing import TYPE_CHECKING

import httpx2
import pandas as pd

from index_core.sources.base import PRICE_COLUMNS, SourceError


if TYPE_CHECKING:
    from datetime import date

BASE_URL = "https://api.tiingo.com"
KEY_VARIABLE = "TIINGO_API_KEY"

_FIELDS = {
    "close": "close",
    "volume": "volume",
    "adjOpen": "adj_open",
    "adjHigh": "adj_high",
    "adjLow": "adj_low",
    "adjClose": "adj_close",
    "adjVolume": "adj_volume",
}


class TiingoSource:
    name = "tiingo"

    def __init__(
        self, api_key: str, transport: httpx2.BaseTransport | None = None
    ) -> None:
        self._client = httpx2.Client(
            base_url=BASE_URL,
            headers={"Authorization": f"Token {api_key}"},
            timeout=30.0,
            transport=transport,
        )

    @classmethod
    def from_env(cls) -> TiingoSource:
        api_key = os.environ.get(KEY_VARIABLE)
        if not api_key:
            raise SourceError(f"{KEY_VARIABLE} is not set")
        return cls(api_key)

    def fetch(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        try:
            response = self._client.get(
                f"/tiingo/daily/{ticker}/prices",
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
        if response.status_code != 200:
            raise SourceError(
                f"Tiingo returned HTTP {response.status_code} for {ticker}: "
                f"{response.text[:200]}"
            )
        try:
            bars = response.json()
        except ValueError as error:
            raise SourceError(f"Tiingo sent invalid JSON for {ticker}") from error
        if not bars:
            raise SourceError(f"Tiingo has no prices for {ticker} in {start}..{end}")

        frame = pd.DataFrame(bars)
        missing = {"date", *_FIELDS} - set(frame.columns)
        if missing:
            raise SourceError(f"Tiingo bars for {ticker} lack {sorted(missing)}")
        dates = pd.to_datetime(frame["date"], utc=True).dt.tz_localize(None)
        frame = frame.rename(columns=_FIELDS)[list(PRICE_COLUMNS)]
        frame.index = pd.DatetimeIndex(dates.dt.normalize(), name="date")
        return frame.astype(float).sort_index()
