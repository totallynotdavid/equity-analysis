import threading

from contextlib import contextmanager
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

import httpx2
import pytest

from index_core.sources.base import SourceError
from index_core.sources.edgar import EdgarSource
from index_core.sources.french import FamaFrenchSource
from index_core.sources.tiingo import TiingoSource
from index_core.sources.transport import NetworkTransport


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


PROXY_VARIABLES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)


@contextmanager
def serve(body: bytes) -> Iterator[tuple[str, list[str]]]:
    """A local server that records the request line of every request it gets.

    It answers a plain request with `body` and refuses a CONNECT, which is how a
    client asks a proxy for an https host.
    """
    seen: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append(f"GET {self.path}")
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_CONNECT(self) -> None:
            seen.append(f"CONNECT {self.path}")
            self.send_error(502)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture(autouse=True)
def _no_inherited_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in PROXY_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def _fetch_edgar(transport: httpx2.BaseTransport | None = None) -> None:
    source = EdgarSource("test agent", transport, ciks={"AAPL": (320193,)})
    source.facts("AAPL", date(2024, 1, 1))


def _fetch_prices(transport: httpx2.BaseTransport | None = None) -> None:
    source = TiingoSource("key", transport)
    source.fetch("AAPL", date(2024, 1, 1), date(2024, 1, 31))


def _fetch_listings(transport: httpx2.BaseTransport | None = None) -> None:
    TiingoSource("key", transport).listings()


def _fetch_factors(transport: httpx2.BaseTransport | None = None) -> None:
    FamaFrenchSource(transport).fetch()


SOURCES = [
    (_fetch_edgar, "data.sec.gov"),
    (_fetch_prices, "api.tiingo.com"),
    (_fetch_listings, "apimedia.tiingo.com"),
    (_fetch_factors, "mba.tuck.dartmouth.edu"),
]


@pytest.mark.parametrize(("fetch", "host"), SOURCES)
def test_a_source_reaches_its_host_through_the_environment_proxy(
    monkeypatch: pytest.MonkeyPatch,
    fetch: Callable[[], None],
    host: str,
) -> None:
    with serve(b"") as (proxy, seen):
        monkeypatch.setenv("HTTPS_PROXY", proxy)
        with pytest.raises(SourceError):
            fetch()

    assert seen == [f"CONNECT {host}:443"]


@pytest.mark.parametrize(("fetch", "host"), SOURCES)
def test_a_source_uses_the_transport_it_is_given_before_any_proxy(
    monkeypatch: pytest.MonkeyPatch,
    fetch: Callable[[httpx2.BaseTransport], None],
    host: str,
) -> None:
    reached: list[str] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        reached.append(request.url.host)
        return httpx2.Response(500)

    with serve(b"") as (proxy, via_proxy):
        monkeypatch.setenv("HTTPS_PROXY", proxy)
        with pytest.raises(SourceError):
            fetch(httpx2.MockTransport(respond))

    assert reached == [host]
    assert via_proxy == []


def test_a_proxy_url_without_a_scheme_is_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with serve(b"") as (proxy, seen):
        monkeypatch.setenv("HTTPS_PROXY", proxy.removeprefix("http://"))
        with (
            httpx2.Client(transport=NetworkTransport()) as client,
            pytest.raises(httpx2.HTTPError),
        ):
            client.get("https://data.sec.gov/")

    assert seen == ["CONNECT data.sec.gov:443"]


def test_a_request_goes_through_the_proxy_unless_no_proxy_names_its_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with (
        serve(b"from the proxy") as (proxy, via_proxy),
        serve(b"from the origin") as (origin, direct),
    ):
        monkeypatch.setenv("HTTP_PROXY", proxy)
        with httpx2.Client(transport=NetworkTransport()) as client:
            proxied = client.get(f"{origin}/page").text
        monkeypatch.setenv("NO_PROXY", "127.0.0.1")
        with httpx2.Client(transport=NetworkTransport()) as client:
            bypassed = client.get(f"{origin}/page").text

    assert (proxied, bypassed) == ("from the proxy", "from the origin")
    assert via_proxy == [f"GET {origin}/page"]
    assert direct == ["GET /page"]


def test_no_proxy_bypasses_every_host_in_a_cidr_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with serve(b"") as (proxy, via_proxy):
        monkeypatch.setenv("HTTP_PROXY", proxy)
        monkeypatch.setenv("NO_PROXY", "10.0.0.0/8")
        with (
            httpx2.Client(transport=NetworkTransport(), timeout=0.5) as client,
            pytest.raises(httpx2.TransportError),
        ):
            client.get("http://10.1.2.3/")

    assert via_proxy == []
