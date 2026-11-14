import json

from datetime import date
from pathlib import Path

import httpx2
import pytest

from bvl import smv


FIXTURES = Path(__file__).parent / "fixtures"


def answer(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class Scripted(httpx2.BaseTransport):
    """Answers each request with the next body in `bodies`."""

    def __init__(self, bodies: list[bytes]) -> None:
        self.bodies = bodies
        self.requests: list[httpx2.Request] = []

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(200, content=self.bodies.pop(0))


def client(transport: Scripted, cache: Path | None = None) -> smv.SmvClient:
    return smv.SmvClient(cache, transport=transport, sleep=lambda _: None)


def test_parse_envelope_reads_rows_under_either_key() -> None:
    assert len(smv.parse_envelope(json.loads(answer("quotes.json")))) == 3
    statements = {"ResponseCode": "OK", "data": [{"RPJ": "B30150"}]}
    assert smv.parse_envelope(statements) == [{"RPJ": "B30150"}]


def test_parse_envelope_treats_code_108_as_no_rows() -> None:
    assert smv.parse_envelope(json.loads(answer("no_rows.json"))) == []
    assert smv.parse_envelope({"ResponseCode": "108", "Message": "x"}) == []


def test_parse_envelope_raises_on_a_refusal() -> None:
    refused = {"ResponseCode": "50004", "Message": "rango menor o igual a 90 dias"}
    with pytest.raises(smv.SmvError) as raised:
        smv.parse_envelope(refused)
    assert not isinstance(raised.value, smv.TransientError)


def test_parse_envelope_treats_a_locked_log_under_code_minus_one_as_transient() -> None:
    locked = {
        "ResponseCode": "-1",
        "Message": "The process cannot access the file 'LogInfo_20261114.txt' "
        "because it is being used by another process.",
    }
    with pytest.raises(smv.TransientError):
        smv.parse_envelope(locked)


def test_windows_cover_the_span_in_90_day_steps() -> None:
    spans = list(smv.windows(date(2024, 1, 1), date(2024, 12, 31)))
    assert spans[0] == (date(2024, 1, 1), date(2024, 3, 30))
    assert spans[-1][1] == date(2024, 12, 31)
    assert sum((b - a).days + 1 for a, b in spans) == 366
    assert all((b - a).days < smv.MAX_WINDOW_DAYS for a, b in spans)


def test_client_retries_a_locked_log_then_returns_rows() -> None:
    transport = Scripted([answer("log_locked.json")] * 2 + [answer("quotes.json")])
    rows = client(transport).rows("valores/Cotizaciones", {}, keep=False)
    assert len(rows) == 3
    assert len(transport.requests) == 3


def test_client_gives_up_after_its_attempts() -> None:
    transport = Scripted([answer("log_locked.json")] * 8)
    with pytest.raises(smv.TransientError):
        client(transport).rows("valores/Cotizaciones", {}, keep=False)


def test_client_serves_a_kept_answer_from_disk(tmp_path: Path) -> None:
    transport = Scripted([answer("quotes.json")])
    params = {"sFechaInicio": "01/01/2025", "sFechaFin": "31/03/2025"}
    first = client(transport, tmp_path).rows("valores/Cotizaciones", params, keep=True)
    again = client(Scripted([]), tmp_path).rows(
        "valores/Cotizaciones", params, keep=True
    )
    assert again == first


def test_client_does_not_keep_an_unsettled_answer(tmp_path: Path) -> None:
    transport = Scripted([answer("quotes.json"), answer("quotes.json")])
    fetcher = client(transport, tmp_path)
    fetcher.rows("valores/Cotizaciones", {}, keep=False)
    fetcher.rows("valores/Cotizaciones", {}, keep=False)
    assert len(transport.requests) == 2
    assert list(tmp_path.iterdir()) == []


def test_a_window_settles_a_week_after_it_closes() -> None:
    assert smv.settled(date(2026, 11, 1), today=date(2026, 11, 14))
    assert not smv.settled(date(2026, 11, 10), today=date(2026, 11, 14))
