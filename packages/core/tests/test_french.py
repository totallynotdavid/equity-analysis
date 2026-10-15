import io
import zipfile

from typing import TYPE_CHECKING

import httpx2
import pandas as pd
import pytest

from index_core.sources.base import FACTOR_COLUMNS, SourceError
from index_core.sources.french import URL, FamaFrenchSource, parse_factors


if TYPE_CHECKING:
    from pathlib import Path


PREAMBLE = (
    "This file was created by CMPT_ME_BEME_RETS_DAILY using the 202608 CRSP "
    "database.\r\n\r\n"
)
HEADER = ",Mkt-RF,SMB,HML,RMW,CMA,RF\r\n"
ROWS = (
    "20260102,   0.50,  -0.25,   0.10,   0.05,  -0.02,   0.015\r\n"
    "20260105,  -1.20,   0.30,   0.00,  -0.10,   0.04,   0.015\r\n"
)
COPYRIGHT = "\r\n Copyright 2026 Kenneth R. French\r\n"


def _zip(text: str, name: str = "F-F_Research_Data_5_Factors_2x3_daily.csv") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, text)
    return buffer.getvalue()


def _library_file(rows: str = ROWS) -> bytes:
    return _zip(PREAMBLE + HEADER + rows + COPYRIGHT)


def test_the_library_file_becomes_daily_fractions_by_date() -> None:
    frame = parse_factors(_library_file())

    assert list(frame.columns) == list(FACTOR_COLUMNS)
    assert list(frame.index) == [pd.Timestamp("2026-01-02"), pd.Timestamp("2026-01-05")]
    assert frame.index.name == "date"
    assert frame.loc["2026-01-02", "mkt_rf"] == pytest.approx(0.005)
    assert frame.loc["2026-01-05", "mkt_rf"] == pytest.approx(-0.012)
    assert frame.loc["2026-01-05", "rf"] == pytest.approx(0.00015)


@pytest.mark.parametrize(
    ("archive", "message"),
    [
        (b"not a zip", "not a zip file"),
        (_zip(PREAMBLE + ROWS), "no header line"),
        (_zip(PREAMBLE + HEADER + COPYRIGHT), "no data rows"),
        (
            _zip(PREAMBLE + HEADER + "20260102,0.5,x,0.1,0.0,0.0,0.01\r\n"),
            "malformed row",
        ),
        (
            _zip(PREAMBLE + HEADER + "20260102,0.5,-99.99,0.1,0.0,0.0,0.01\r\n"),
            "missing value",
        ),
        (
            _zip(PREAMBLE + HEADER + ROWS + ROWS.splitlines(keepends=True)[0]),
            "not one row per day",
        ),
    ],
    ids=["not-zip", "no-header", "no-rows", "bad-number", "missing", "duplicate-day"],
)
def test_a_file_that_is_not_the_library_file_is_refused(
    archive: bytes, message: str
) -> None:
    with pytest.raises(SourceError, match=message):
        parse_factors(archive)


def _source(
    respond: httpx2.MockTransport, cache: Path | None = None
) -> FamaFrenchSource:
    return FamaFrenchSource(transport=respond, cache=cache)


def test_the_source_downloads_the_five_factor_daily_file() -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, content=_library_file())

    frame = _source(httpx2.MockTransport(respond)).fetch()

    assert [str(request.url) for request in requests] == [URL]
    assert len(frame) == 2


def test_an_http_error_is_a_source_error() -> None:
    source = _source(httpx2.MockTransport(lambda _: httpx2.Response(503)))

    with pytest.raises(SourceError, match="HTTP 503"):
        source.fetch()


def test_a_repeat_fetch_inside_the_cache_window_does_not_download_again(
    tmp_path: Path,
) -> None:
    calls = 0

    def respond(_: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(200, content=_library_file())

    source = _source(httpx2.MockTransport(respond), tmp_path)

    first, second = source.fetch(), source.fetch()

    assert calls == 1
    pd.testing.assert_frame_equal(first, second)
