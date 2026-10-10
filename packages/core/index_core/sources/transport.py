"""HTTP transports shared by the sources: pacing and an on-disk response cache.

A source stacks them as `CachingTransport(PacedTransport(network))`, so a
response served from disk costs neither a wait nor a request.
"""

import hashlib
import os
import tempfile
import time

from datetime import timedelta
from typing import TYPE_CHECKING

import httpx2


if TYPE_CHECKING:
    from pathlib import Path

DEFAULT_MAX_AGE = timedelta(days=1)
# A 200 body is kept as `.json`, a 404 as `.404`.
_KEPT = {200: ".json", 404: ".404"}


class PacedTransport(httpx2.BaseTransport):
    """Leaves `min_interval` seconds between the starts of two requests."""

    def __init__(self, inner: httpx2.BaseTransport, min_interval: float) -> None:
        self._inner = inner
        self._min_interval = min_interval
        self._last_request: float | None = None

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        if self._last_request is not None:
            wait = self._last_request + self._min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
        self._last_request = time.monotonic()
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()


class CachingTransport(httpx2.BaseTransport):
    """Keeps 200 and 404 responses on disk and fetches an entry older than
    `max_age` again. A 404 is an answer about the ticker, not a failure.
    """

    def __init__(
        self,
        inner: httpx2.BaseTransport,
        directory: Path,
        max_age: timedelta = DEFAULT_MAX_AGE,
    ) -> None:
        self._inner = inner
        self._directory = directory
        self._max_age = max_age

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        for status, suffix in _KEPT.items():
            path = self._path(request, suffix)
            if self._fresh(path):
                return httpx2.Response(
                    status,
                    content=path.read_bytes(),
                    headers={"content-type": "application/json"},
                )
        response = self._inner.handle_request(request)
        kept = _KEPT.get(response.status_code)
        if kept is not None:
            self._write(self._path(request, kept), response.read())
        return response

    def close(self) -> None:
        self._inner.close()

    def _write(self, path: Path, body: bytes) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        descriptor, partial = tempfile.mkstemp(dir=self._directory, suffix=".partial")
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(body)
            os.replace(partial, path)
        except BaseException:
            os.unlink(partial)
            raise

    def _path(self, request: httpx2.Request, suffix: str) -> Path:
        key = f"{request.method} {request.url}".encode()
        digest = hashlib.sha256(key).hexdigest()[:24]
        return self._directory / f"{request.url.host}-{digest}{suffix}"

    def _fresh(self, path: Path) -> bool:
        try:
            age = time.time() - path.stat().st_mtime
        except FileNotFoundError:
            return False
        return age < self._max_age.total_seconds()
