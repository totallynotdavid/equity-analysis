"""Fama-French daily five-factor returns from Kenneth French's data library.

`parse_factors` reads the zip the library serves. `FamaFrenchSource` fetches it
through the cached transport of `transport.py`.
"""

import io
import re
import zipfile

from typing import TYPE_CHECKING

import httpx2
import pandas as pd

from index_core.sources.base import FACTOR_COLUMNS, SourceError
from index_core.sources.transport import CachingTransport


if TYPE_CHECKING:
    from pathlib import Path

URL = (
    "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
    "F-F_Research_Data_5_Factors_2x3_daily_CSV.zip"
)
_HEADER = ("", "Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF")
_ROW = re.compile(r"^\d{8},")


def parse_factors(archive: bytes) -> pd.DataFrame:
    """The daily factors of the library's zip, as fractions.

    The file has a free-text preamble, a header line, one `YYYYMMDD` row a day,
    and a copyright line. The library writes percent.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            names = zipped.namelist()
            text = zipped.read(names[0]).decode("latin-1")
    except (zipfile.BadZipFile, IndexError) as error:
        raise SourceError("the Fama-French download is not a zip file") from error

    lines = [line.strip() for line in text.splitlines()]
    try:
        header = next(
            number
            for number, line in enumerate(lines)
            if tuple(field.strip() for field in line.split(",")) == _HEADER
        )
    except StopIteration:
        raise SourceError(
            f"the Fama-French file has no header line `{','.join(_HEADER)}`"
        ) from None
    rows = [line for line in lines[header + 1 :] if _ROW.match(line)]
    if not rows:
        raise SourceError("the Fama-French file has no data rows")

    try:
        table = pd.DataFrame(
            [row.split(",") for row in rows], columns=["date", *FACTOR_COLUMNS]
        )
        frame = table[list(FACTOR_COLUMNS)].astype(float)
        frame.index = pd.DatetimeIndex(
            pd.to_datetime(table["date"], format="%Y%m%d"), name="date"
        )
    except ValueError as error:
        raise SourceError(
            f"the Fama-French file has a malformed row: {error}"
        ) from error
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise SourceError("the Fama-French file is not one row per day in order")
    # The library marks a missing value -99.99 or -999.
    if (frame <= -99).any(axis=None):
        raise SourceError("the Fama-French file has a missing value")
    return frame / 100


class FamaFrenchSource:
    name = "french"

    def __init__(
        self, transport: httpx2.BaseTransport | None = None, cache: Path | None = None
    ) -> None:
        inner: httpx2.BaseTransport = transport or httpx2.HTTPTransport()
        if cache is not None:
            inner = CachingTransport(inner, cache)
        self._client = httpx2.Client(timeout=60.0, transport=inner)

    @classmethod
    def from_env(cls, cache: Path | None = None) -> FamaFrenchSource:
        return cls(cache=cache)

    def fetch(self) -> pd.DataFrame:
        try:
            response = self._client.get(URL)
        except httpx2.HTTPError as error:
            raise SourceError(
                f"could not reach the Fama-French library: {error!r}"
            ) from error
        if response.status_code != 200:
            raise SourceError(
                f"the Fama-French library returned HTTP {response.status_code}"
            )
        return parse_factors(response.content)
