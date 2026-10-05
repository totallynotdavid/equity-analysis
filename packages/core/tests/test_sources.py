import json

from datetime import date

import httpx2
import pandas as pd
import pytest

from index_core.sources.base import PRICE_COLUMNS, SourceError
from index_core.sources.synthetic import SyntheticSource
from index_core.sources.tiingo import TiingoSource


TIINGO_BARS = [
    {
        "date": "2024-03-04T00:00:00.000Z",
        "close": 181.16,
        "high": 182.0,
        "low": 179.9,
        "open": 176.15,
        "volume": 81510101,
        "adjClose": 180.1,
        "adjHigh": 181.0,
        "adjLow": 178.9,
        "adjOpen": 175.1,
        "adjVolume": 81510101,
        "divCash": 0.0,
        "splitFactor": 1.0,
    },
    {
        "date": "2024-03-05T00:00:00.000Z",
        "close": 179.66,
        "high": 180.5,
        "low": 177.8,
        "open": 180.0,
        "volume": 95132355,
        "adjClose": 178.6,
        "adjHigh": 179.5,
        "adjLow": 176.8,
        "adjOpen": 179.0,
        "adjVolume": 95132355,
        "divCash": 0.0,
        "splitFactor": 1.0,
    },
]


def _tiingo(
    handler: httpx2.MockTransport | None = None,
) -> tuple[TiingoSource, list[httpx2.Request]]:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, content=json.dumps(TIINGO_BARS))

    transport = handler or httpx2.MockTransport(respond)
    return TiingoSource("secret-key", transport=transport), requests


def test_tiingo_asks_for_one_ticker_with_the_key_in_the_header() -> None:
    source, requests = _tiingo()

    source.fetch("AAPL", date(2024, 3, 1), date(2024, 3, 8))

    (request,) = requests
    assert request.url.path == "/tiingo/daily/AAPL/prices"
    assert request.url.params["startDate"] == "2024-03-01"
    assert request.url.params["endDate"] == "2024-03-08"
    assert request.headers["Authorization"] == "Token secret-key"


def test_tiingo_bars_become_the_standard_frame() -> None:
    source, _ = _tiingo()

    bars = source.fetch("AAPL", date(2024, 3, 1), date(2024, 3, 8))

    assert list(bars.columns) == list(PRICE_COLUMNS)
    assert bars.index.name == "date"
    assert list(bars.index) == [pd.Timestamp("2024-03-04"), pd.Timestamp("2024-03-05")]
    assert bars.loc["2024-03-04", "adj_close"] == 180.1
    assert bars.loc["2024-03-04", "close"] == 181.16
    assert bars.loc["2024-03-05", "adj_volume"] == 95132355


def test_tiingo_error_status_is_a_source_error() -> None:
    source, _ = _tiingo(
        httpx2.MockTransport(lambda _: httpx2.Response(404, text="Not found"))
    )

    with pytest.raises(SourceError, match="HTTP 404 for NOPE"):
        source.fetch("NOPE", date(2024, 3, 1), date(2024, 3, 8))


def test_tiingo_with_no_bars_is_a_source_error() -> None:
    source, _ = _tiingo(httpx2.MockTransport(lambda _: httpx2.Response(200, json=[])))

    with pytest.raises(SourceError, match="no prices for AAPL"):
        source.fetch("AAPL", date(2024, 3, 1), date(2024, 3, 8))


def test_tiingo_key_is_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    with pytest.raises(SourceError, match="TIINGO_API_KEY is not set"):
        TiingoSource.from_env()

    monkeypatch.setenv("TIINGO_API_KEY", "from-env")
    assert isinstance(TiingoSource.from_env(), TiingoSource)


def test_tiingo_network_failure_is_a_source_error() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no route", request=request)

    source, _ = _tiingo(httpx2.MockTransport(refuse))

    with pytest.raises(SourceError, match="could not reach Tiingo for AAPL"):
        source.fetch("AAPL", date(2024, 3, 1), date(2024, 3, 8))


def test_tiingo_malformed_bodies_are_source_errors() -> None:
    not_json, _ = _tiingo(
        httpx2.MockTransport(lambda _: httpx2.Response(200, text="<"))
    )
    no_fields, _ = _tiingo(
        httpx2.MockTransport(
            lambda _: httpx2.Response(200, json=[{"date": "2024-03-04"}])
        )
    )

    with pytest.raises(SourceError, match="invalid JSON for AAPL"):
        not_json.fetch("AAPL", date(2024, 3, 1), date(2024, 3, 8))
    with pytest.raises(SourceError, match=r"lack \['adjClose'"):
        no_fields.fetch("AAPL", date(2024, 3, 1), date(2024, 3, 8))


def test_synthetic_prices_do_not_depend_on_the_requested_range() -> None:
    source = SyntheticSource()

    wide = source.fetch("AAPL", date(2023, 1, 2), date(2024, 6, 28))
    late_start = source.fetch("AAPL", date(2024, 1, 2), date(2024, 6, 28))
    early_end = source.fetch("AAPL", date(2023, 1, 2), date(2024, 3, 29))

    pd.testing.assert_frame_equal(wide.loc["2024-01-02":], late_start)
    pd.testing.assert_frame_equal(wide.loc[:"2024-03-29"], early_end)


def test_synthetic_prices_before_the_epoch_are_a_source_error() -> None:
    with pytest.raises(SourceError, match="start at 2000-01-03"):
        SyntheticSource().fetch("AAPL", date(1999, 12, 1), date(2024, 1, 31))


def test_synthetic_prices_are_repeatable_and_consistent() -> None:
    source = SyntheticSource()

    first = source.fetch("AAPL", date(2024, 1, 1), date(2024, 6, 30))
    second = source.fetch("AAPL", date(2024, 1, 1), date(2024, 6, 30))
    other = source.fetch("MSFT", date(2024, 1, 1), date(2024, 6, 30))

    pd.testing.assert_frame_equal(first, second)
    assert not first["close"].equals(other["close"])
    assert list(first.columns) == list(PRICE_COLUMNS)
    assert first.index.is_monotonic_increasing
    assert (first["adj_high"] >= first[["adj_open", "adj_close"]].max(axis=1)).all()
    assert (first["adj_low"] <= first[["adj_open", "adj_close"]].min(axis=1)).all()
