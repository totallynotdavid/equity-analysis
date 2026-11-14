"""Client for the SMV open data API at `mvnet.smv.gob.pe`.

The API needs no key. Every answer is a JSON envelope with a `ResponseCode`:
`"0"` or `"OK"` carries rows, `108` means no rows for the query, and
`-2147024864` is the server failing to open its own log file. The log failure
also comes back as code `-1` with a "being used by another process" message.
It passes on a retry, and it answers about a third of requests on a busy day, so
the client retries it with backoff.

A query whose dates closed more than `SETTLED` ago never changes, so its answer
is kept gzipped on disk and read from there next time.
"""

import gzip
import hashlib
import json
import logging
import os
import tempfile
import time

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import httpx2


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping
    from pathlib import Path

BASE_URL = "https://mvnet.smv.gob.pe/SMV.OData.Api/api/"
# The open data portal in front of the API turns away clients that do not look
# like a browser.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)
# Quote and event queries accept at most 90 days.
MAX_WINDOW_DAYS = 90
# The SMV keeps correcting a filing for a few days after it is made.
SETTLED = timedelta(days=7)

_OK = frozenset({"0", "OK"})
_NO_ROWS = "108"
_LOG_LOCKED = "-2147024864"
_LOG_LOCKED_MESSAGE = "being used by another process"

Record = dict[str, Any]

log = logging.getLogger(__name__)


class SmvError(Exception):
    """The API refused the query, for a reason a retry will not change."""


class TransientError(SmvError):
    """The API failed in a way that passes on a retry."""


def parse_envelope(body: Mapping[str, Any]) -> list[Record]:
    """Return the rows of one API answer, or raise on a refusal."""
    code = str(body.get("ResponseCode"))
    message = str(body.get("Message"))
    if code in _OK:
        rows = body.get("Resultado", body.get("data"))
        return list(rows or [])
    if _NO_ROWS in (code, message):
        return []
    if code == _LOG_LOCKED or _LOG_LOCKED_MESSAGE in message:
        raise TransientError(message)
    raise SmvError(f"{code}: {message}")


def smv_date(day: date) -> str:
    return day.strftime("%d/%m/%Y")


def windows(
    start: date, end: date, days: int = MAX_WINDOW_DAYS
) -> Iterator[tuple[date, date]]:
    """Split `start..end`, both included, into spans of at most `days` days."""
    while start <= end:
        last = min(start + timedelta(days=days - 1), end)
        yield start, last
        start = last + timedelta(days=1)


def settled(last_day: date, today: date) -> bool:
    return today - last_day > SETTLED


class SmvClient:
    """Fetches API rows, retrying transient failures and caching settled answers."""

    def __init__(
        self,
        cache_dir: Path | None,
        *,
        transport: httpx2.BaseTransport | None = None,
        attempts: int = 8,
        backoff: float = 1.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = httpx2.Client(
            base_url=BASE_URL,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=120,
            transport=transport,
        )
        self._cache_dir = cache_dir
        self._attempts = attempts
        self._backoff = backoff
        self._sleep = sleep

    def close(self) -> None:
        self._http.close()

    def rows(self, path: str, params: Mapping[str, str], *, keep: bool) -> list[Record]:
        """Return the rows for one query. `keep` caches an answer that cannot change."""
        cached = self._cache_path(path, params) if keep else None
        if cached is not None and cached.exists():
            return parse_envelope(json.loads(gzip.decompress(cached.read_bytes())))
        body = self._fetch(path, params)
        rows = parse_envelope(body)
        if cached is not None:
            _write_atomic(cached, gzip.compress(json.dumps(body).encode()))
        return rows

    def _fetch(self, path: str, params: Mapping[str, str]) -> Mapping[str, Any]:
        delay = self._backoff
        for attempt in range(1, self._attempts + 1):
            try:
                body = self._get(path, params)
                parse_envelope(body)
                return body
            except (TransientError, httpx2.TransportError) as error:
                if attempt == self._attempts:
                    raise
                log.debug("retry %s %s: %s", path, dict(params), error)
            self._sleep(delay)
            delay = min(delay * 2, 60)
        raise AssertionError("unreachable")

    def _get(self, path: str, params: Mapping[str, str]) -> Mapping[str, Any]:
        response = self._http.get(path, params=dict(params))
        if response.status_code >= 500:
            raise TransientError(f"HTTP {response.status_code}")
        response.raise_for_status()
        body: Mapping[str, Any] = response.json()
        return body

    def _cache_path(self, path: str, params: Mapping[str, str]) -> Path | None:
        if self._cache_dir is None:
            return None
        query = urlencode(sorted(params.items()))
        digest = hashlib.sha256(f"{path}?{query}".encode()).hexdigest()[:20]
        return self._cache_dir / f"{path.replace('/', '-')}-{digest}.json.gz"


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, partial = tempfile.mkstemp(dir=path.parent, suffix=".partial")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
        os.replace(partial, path)
    except BaseException:
        os.unlink(partial)
        raise


def quotes(client: SmvClient, start: date, end: date, today: date) -> list[Record]:
    """Daily BVL quotes for `start..end`, one row per ticker and trading day."""
    return client.rows(
        "valores/Cotizaciones",
        {"sFechaInicio": smv_date(start), "sFechaFin": smv_date(end)},
        keep=settled(end, today),
    )


def index_levels(client: SmvClient, day: date, today: date) -> list[Record]:
    """Closing levels of every BVL index on one day."""
    return client.rows(
        "valores/Indices", {"sFechaIndice": smv_date(day)}, keep=settled(day, today)
    )


def events(client: SmvClient, start: date, end: date, today: date) -> list[Record]:
    """Material events (hechos de importancia) issuers filed in `start..end`."""
    return client.rows(
        "registro/HechosSMVEmisor",
        {"sFechaInicio": smv_date(start), "sFechaFin": smv_date(end)},
        keep=settled(end, today),
    )
