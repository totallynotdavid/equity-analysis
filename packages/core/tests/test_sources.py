import json
import os

from datetime import date
from pathlib import Path

import httpx2
import pandas as pd
import pytest

from index_core.sources.base import PRICE_COLUMNS, SourceError
from index_core.sources.synthetic import SyntheticSource
from index_core.sources.tiingo import KEY_VARIABLE, TiingoSource
from index_core.store import Store


FIXTURES = Path(__file__).parent / "fixtures"
RECORDED_SPLIT = FIXTURES / "recorded_aapl_prices.json"
# Holds AAPL's dividend of 2020-08-07 and split of 2020-08-31. The command in
# `record_tiingo_prices.py` records the same window.
SPLIT_RANGE = (date(2020, 7, 31), date(2020, 9, 1))

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
    assert (bars["div_cash"] == 0.0).all()
    assert (bars["split_factor"] == 1.0).all()


@pytest.mark.parametrize(
    "payload",
    [
        FIXTURES / "aapl_prices_handwritten.json",
        pytest.param(
            RECORDED_SPLIT,
            marks=pytest.mark.skipif(
                not RECORDED_SPLIT.exists(),
                reason="not recorded; run tests/record_tiingo_prices.py with a key",
            ),
        ),
    ],
    ids=["handwritten", "recorded"],
)
def test_a_split_and_a_dividend_survive_the_fetch_and_the_store(
    payload: Path, tmp_path: Path
) -> None:
    # AAPL split 4-for-1 with the ex-date 2020-08-31 and paid 0.82 with the
    # ex-date 2020-08-07.
    source, _ = _tiingo(
        httpx2.MockTransport(
            lambda _: httpx2.Response(200, content=payload.read_text())
        )
    )

    bars = source.fetch("AAPL", *SPLIT_RANGE)
    with Store.open(tmp_path / "db.sqlite") as store:
        store.upsert_prices("tiingo", "AAPL", bars)
        stored = store.read_prices(["AAPL"]).set_index("date")

    assert stored.loc["2020-08-31", "split_factor"] == 4.0
    assert stored.loc["2020-08-07", "div_cash"] == pytest.approx(0.82)
    assert stored["split_factor"].drop(pd.Timestamp("2020-08-31")).eq(1.0).all()
    assert stored["split_factor"].prod() == 4.0
    closes = stored["close"]
    assert closes["2020-08-28"] > 3 * float(closes["2020-09-01"])


def test_a_bar_without_split_or_dividend_fields_is_a_source_error() -> None:
    bars = [{k: v for k, v in TIINGO_BARS[0].items() if k != "splitFactor"}]
    source, _ = _tiingo(httpx2.MockTransport(lambda _: httpx2.Response(200, json=bars)))

    with pytest.raises(SourceError, match=r"lack \['splitFactor'\]"):
        source.fetch("AAPL", date(2024, 3, 1), date(2024, 3, 8))


@pytest.mark.parametrize("ticker", ["BRK.B", "brk.b", "BRK-B"])
def test_tiingo_is_asked_for_a_share_class_with_a_dash(ticker: str) -> None:
    source, requests = _tiingo()

    source.fetch(ticker, date(2024, 3, 1), date(2024, 3, 8))

    assert requests[0].url.path == "/tiingo/daily/BRK-B/prices"


@pytest.mark.skipif(
    not os.environ.get(KEY_VARIABLE), reason=f"{KEY_VARIABLE} is not set"
)
def test_live_tiingo_reports_the_aapl_split() -> None:
    bars = TiingoSource.from_env().fetch("AAPL", *SPLIT_RANGE)

    assert list(bars.columns) == list(PRICE_COLUMNS)
    assert bars.loc["2020-08-07", "div_cash"] == pytest.approx(0.82)
    assert bars.loc["2020-08-31", "split_factor"] == 4.0
    assert bars["split_factor"].drop(pd.Timestamp("2020-08-31")).eq(1.0).all()
    assert bars["close"]["2020-08-28"] > 3 * float(bars["close"]["2020-09-01"])
    # Tiingo adjusts volume for splits: the day before trades 4 times as many.
    assert bars["adj_volume"]["2020-08-28"] == pytest.approx(
        4 * float(bars["volume"]["2020-08-28"])
    )


@pytest.mark.skipif(
    not os.environ.get(KEY_VARIABLE), reason=f"{KEY_VARIABLE} is not set"
)
def test_live_tiingo_serves_a_dotted_ticker() -> None:
    bars = TiingoSource.from_env().fetch("BRK.B", date(2024, 3, 1), date(2024, 3, 8))

    assert not bars.empty


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


def test_tiingo_json_object_instead_of_bars_is_a_source_error() -> None:
    source, _ = _tiingo(
        httpx2.MockTransport(
            lambda _: httpx2.Response(200, json={"detail": "Not found."})
        )
    )

    with pytest.raises(SourceError, match=r"Not found.*for AAPL, not bars"):
        source.fetch("AAPL", date(2024, 3, 1), date(2024, 3, 8))


def test_a_reversed_range_is_a_source_error_without_a_request() -> None:
    tiingo, requests = _tiingo()

    with pytest.raises(SourceError, match="start 2024-03-08 is after end 2024-03-01"):
        tiingo.fetch("AAPL", date(2024, 3, 8), date(2024, 3, 1))
    with pytest.raises(SourceError, match="start 2024-03-08 is after end 2024-03-01"):
        SyntheticSource().fetch("AAPL", date(2024, 3, 8), date(2024, 3, 1))
    assert requests == []


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
    assert (first["div_cash"] == 0.0).all()
    assert (first["split_factor"] == 1.0).all()
    assert (first["adj_high"] >= first[["adj_open", "adj_close"]].max(axis=1)).all()
    assert (first["adj_low"] <= first[["adj_open", "adj_close"]].min(axis=1)).all()
