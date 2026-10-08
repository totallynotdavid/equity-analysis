import json
import time

from datetime import date
from itertools import pairwise
from pathlib import Path

import httpx2
import pandas as pd
import pytest

from index_core.sources.base import FACT_COLUMNS, NotFoundError, SourceError
from index_core.sources.edgar import CONCEPTS, EdgarSource, parse_companyfacts


FIXTURE = Path(__file__).parent / "fixtures" / "acme_companyfacts_handwritten.json"
TICKERS = {
    "0": {"cik_str": 1234567, "ticker": "ACME", "title": "Acme Corp"},
    "1": {"cik_str": 1067983, "ticker": "BRK-B", "title": "Berkshire Hathaway"},
}


def _payload() -> dict[str, object]:
    return json.loads(FIXTURE.read_text())  # type: ignore[no-any-return]


def _value(
    facts: pd.DataFrame, concept: str, end: str, filed: str, start: str | None = None
) -> float:
    row = facts[
        (facts["concept"] == concept)
        & (facts["end"] == end)
        & (facts["filed"] == filed)
        & (facts["start"].isna() if start is None else facts["start"] == start)
    ]
    assert len(row) == 1
    return float(row["value"].iloc[0])


def test_the_fixture_parses_to_one_row_per_fact_per_filing() -> None:
    facts = parse_companyfacts(_payload(), "ACME")

    assert list(facts.columns) == list(FACT_COLUMNS)
    assert str(facts["filed"].dtype).startswith("datetime64")
    # FY2022 revenue appears as filed in the 10-K and as restated in the 10-K/A.
    assert _value(facts, "revenue", "2022-12-31", "2023-02-15", "2022-01-01") == 400
    assert _value(facts, "revenue", "2022-12-31", "2023-06-01", "2022-01-01") == 405
    assert _value(facts, "total_assets", "2023-09-30", "2023-11-03") == 1000


def test_only_audited_forms_count() -> None:
    facts = parse_companyfacts(_payload(), "ACME")

    assert 999 not in set(facts["value"])


def test_capex_is_the_amount_paid_whichever_sign_the_filer_used() -> None:
    expected = parse_companyfacts(_payload(), "ACME")
    flipped = _payload()
    tag = flipped["facts"]["us-gaap"]["PaymentsToAcquirePropertyPlantAndEquipment"]  # type: ignore[index]
    for entry in tag["units"]["USD"]:
        entry["val"] = -entry["val"]

    facts = parse_companyfacts(flipped, "ACME")

    capex = facts[facts["concept"] == "capex"]
    assert not capex.empty
    assert (capex["value"] > 0).all()
    pd.testing.assert_frame_equal(facts, expected)


def test_a_tag_change_keeps_both_tags_and_their_priority() -> None:
    facts = parse_companyfacts(_payload(), "ACME")
    revenue = facts[facts["concept"] == "revenue"]

    old = revenue[revenue["end"] == "2022-12-31"]
    new = revenue[revenue["end"] == "2023-12-31"]
    assert set(old["priority"]) == {0}
    assert set(new["priority"]) == {1}


def test_a_share_count_given_per_class_is_summed() -> None:
    facts = parse_companyfacts(_payload(), "ACME")

    assert _value(facts, "shares", "2023-10-27", "2023-11-03") == 100
    assert _value(facts, "shares", "2022-10-28", "2022-11-04") == 125


def test_tags_and_units_that_no_concept_uses_are_ignored() -> None:
    facts = parse_companyfacts(_payload(), "ACME")

    # The fixture also carries EarningsPerShareDiluted, which no concept reads.
    assert set(facts["concept"]) == set(CONCEPTS)
    payload = _payload()
    payload["facts"]["us-gaap"]["Assets"]["units"] = {"EUR": []}  # type: ignore[index]
    assert "total_assets" not in set(parse_companyfacts(payload)["concept"])


def test_a_document_without_facts_is_a_source_error() -> None:
    with pytest.raises(SourceError, match="no facts for ACME"):
        parse_companyfacts({"cik": 1}, "ACME")
    truncated = {"form": "10-K", "end": "2023-12-31"}
    with pytest.raises(SourceError, match="malformed facts for ACME"):
        parse_companyfacts(
            {"facts": {"us-gaap": {"Assets": {"units": {"USD": [truncated]}}}}}, "ACME"
        )


def test_a_company_with_no_matching_facts_gives_an_empty_frame() -> None:
    facts = parse_companyfacts({"facts": {"us-gaap": {}}})

    assert facts.empty
    assert list(facts.columns) == list(FACT_COLUMNS)


def _edgar(
    requests: list[httpx2.Request] | None = None, min_interval: float = 0.0
) -> EdgarSource:
    def respond(request: httpx2.Request) -> httpx2.Response:
        if requests is not None:
            requests.append(request)
        if request.url.path == "/files/company_tickers.json":
            return httpx2.Response(200, json=TICKERS)
        if request.url.path == "/api/xbrl/companyfacts/CIK0001234567.json":
            return httpx2.Response(200, content=FIXTURE.read_bytes())
        return httpx2.Response(404, text="Not Found")

    return EdgarSource(
        "index tests test@example.com",
        transport=httpx2.MockTransport(respond),
        min_interval=min_interval,
    )


def test_edgar_sends_the_contact_user_agent_and_a_padded_cik() -> None:
    requests: list[httpx2.Request] = []

    _edgar(requests).facts("acme", date(2024, 12, 31))

    paths = [request.url.path for request in requests]
    assert paths == [
        "/files/company_tickers.json",
        "/api/xbrl/companyfacts/CIK0001234567.json",
    ]
    assert {r.headers["User-Agent"] for r in requests} == {
        "index tests test@example.com"
    }


def test_edgar_facts_stop_at_the_end_date() -> None:
    source = _edgar()

    early = source.facts("ACME", date(2023, 6, 1))
    late = source.facts("ACME", date(2024, 12, 31))

    assert early["filed"].max() == pd.Timestamp("2023-06-01")
    assert late["filed"].max() == pd.Timestamp("2024-02-15")


def test_edgar_reads_the_ticker_list_once() -> None:
    requests: list[httpx2.Request] = []
    source = _edgar(requests)

    source.facts("ACME", date(2024, 1, 1))
    source.facts("ACME", date(2024, 1, 1))

    listings = [r for r in requests if r.url.path == "/files/company_tickers.json"]
    assert len(listings) == 1


def test_an_unknown_ticker_or_a_company_without_facts_is_not_found() -> None:
    source = _edgar()

    with pytest.raises(NotFoundError, match="no company for ticker NOPE"):
        source.facts("NOPE", date(2024, 1, 1))
    # The ticker is listed, but its facts endpoint is empty.
    with pytest.raises(NotFoundError, match=r"no facts at .*CIK0001067983"):
        source.facts("BRK.B", date(2024, 1, 1))


def test_other_failures_are_plain_source_errors() -> None:
    server_error = EdgarSource(
        "ua test@example.com",
        transport=httpx2.MockTransport(lambda _: httpx2.Response(503, text="busy")),
    )
    not_json = EdgarSource(
        "ua test@example.com",
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, text="<")),
    )

    with pytest.raises(SourceError, match="HTTP 503") as raised:
        server_error.facts("ACME", date(2024, 1, 1))
    assert not isinstance(raised.value, NotFoundError)
    with pytest.raises(SourceError, match="invalid JSON"):
        not_json.facts("ACME", date(2024, 1, 1))


def test_edgar_network_failure_is_a_source_error() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no route", request=request)

    source = EdgarSource("ua test@example.com", transport=httpx2.MockTransport(refuse))

    with pytest.raises(SourceError, match="could not reach EDGAR"):
        source.facts("ACME", date(2024, 1, 1))


def test_requests_are_spaced_by_the_minimum_interval() -> None:
    requests: list[httpx2.Request] = []
    times: list[float] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        times.append(time.monotonic())
        requests.append(request)
        if request.url.path == "/files/company_tickers.json":
            return httpx2.Response(200, json=TICKERS)
        return httpx2.Response(200, content=FIXTURE.read_bytes())

    source = EdgarSource(
        "ua test@example.com",
        transport=httpx2.MockTransport(respond),
        min_interval=0.2,
    )

    source.facts("ACME", date(2024, 1, 1))
    source.facts("ACME", date(2024, 1, 1))

    assert len(times) == 3
    assert all(later - earlier >= 0.19 for earlier, later in pairwise(times))


def test_the_user_agent_is_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(SourceError, match="SEC_USER_AGENT is not set"):
        EdgarSource.from_env()

    monkeypatch.setenv("SEC_USER_AGENT", "Jane Doe jane@example.com")
    assert isinstance(EdgarSource.from_env(), EdgarSource)
