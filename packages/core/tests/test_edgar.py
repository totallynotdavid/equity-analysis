import json
import time

from datetime import date
from itertools import pairwise
from pathlib import Path
from typing import Any

import httpx2
import pandas as pd
import pytest

from index_core.sources.base import FACT_COLUMNS, NotFoundError, Security, SourceError
from index_core.sources.edgar import (
    CONCEPTS,
    EdgarSource,
    filing_url,
    parse_companyfacts,
)


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
)
FIXTURE = Path(__file__).parent / "fixtures" / "acme_companyfacts_handwritten.json"
RECORDED = Path(__file__).parent / "fixtures" / "recorded_aapl_companyfacts.json"
CIKS = {"ACME": (1234567,), "BRK-B": (1067983,)}
SUBMISSIONS = {
    "cik": "1234567",
    "name": "Acme Corp",
    "sic": "3490",
    "sicDescription": "Miscellaneous Fabricated Metal Products",
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


def test_every_fact_names_the_filing_that_reported_it() -> None:
    facts = parse_companyfacts(_payload(), "ACME")

    restated = facts[
        (facts["concept"] == "revenue") & (facts["filed"] == "2023-06-01")
    ].iloc[0]
    assert restated[["accn", "form", "fy", "fp"]].tolist() == [
        "0001234567-23-000020",
        "10-K/A",
        2022,
        "FY",
    ]
    assert facts["fy"].dtype == "int64"


def test_a_recorded_edgar_payload_parses_into_facts() -> None:
    facts = parse_companyfacts(json.loads(RECORDED.read_text()), "AAPL")

    assert list(facts.columns) == list(FACT_COLUMNS)
    assert {"revenue", "total_assets"} <= set(facts["concept"])
    assert facts["filed"].min() >= pd.Timestamp("2022-01-01")
    assert (facts["value"] > 0).any()


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
    payload = _payload()
    payload["facts"]["us-gaap"]["Unlisted"] = {  # type: ignore[index]
        "units": {"USD": [{"end": "2023-09-30", "val": 1, "filed": "2023-11-03"}]}
    }
    assert set(parse_companyfacts(payload)["concept"]) == set(CONCEPTS)
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
    requests: list[httpx2.Request] | None = None,
    min_interval: float = 0.0,
    ciks: dict[str, tuple[int, ...]] | None = None,
) -> EdgarSource:
    def respond(request: httpx2.Request) -> httpx2.Response:
        if requests is not None:
            requests.append(request)
        if request.url.path == "/api/xbrl/companyfacts/CIK0001234567.json":
            return httpx2.Response(200, content=FIXTURE.read_bytes())
        if request.url.path == "/submissions/CIK0001234567.json":
            return httpx2.Response(200, json=SUBMISSIONS)
        return httpx2.Response(404, text="Not Found")

    return EdgarSource(
        USER_AGENT,
        transport=httpx2.MockTransport(respond),
        min_interval=min_interval,
        ciks=CIKS if ciks is None else ciks,
    )


def test_a_company_is_found_by_the_cik_the_universe_gives_and_only_that() -> None:
    requests: list[httpx2.Request] = []
    source = _edgar(requests, ciks={"acme": (1234567,)})

    facts = source.facts("ACME", date(2024, 12, 31))
    security = source.security("ACME")

    assert not facts.empty
    assert security.cik == 1234567
    assert {request.url.path for request in requests} == {
        "/api/xbrl/companyfacts/CIK0001234567.json",
        "/submissions/CIK0001234567.json",
    }


def test_a_ticker_the_universe_gives_no_cik_is_not_looked_up_anywhere() -> None:
    requests: list[httpx2.Request] = []
    source = _edgar(requests, ciks={"OTHER": (99,)})

    with pytest.raises(NotFoundError, match="no CIK is given for ticker ACME"):
        source.security("ACME")
    with pytest.raises(NotFoundError, match="no CIK is given for ticker ACME"):
        source.facts("ACME", date(2024, 1, 1))

    assert requests == []


def _filed_in_part(payload: dict[str, Any], cik: int, early: bool) -> bytes:
    """The payload as the registrant `cik` filed it: the filings before 2023 if
    `early`, else the others."""
    part = json.loads(json.dumps(payload))
    part["cik"] = cik
    for taxonomy in part["facts"].values():
        for body in taxonomy.values():
            for unit, entries in body["units"].items():
                body["units"][unit] = [
                    entry
                    for entry in entries
                    if (entry["filed"] < "2023-01-01") == early
                ]
    return json.dumps(part).encode()


def test_a_company_that_moved_registrants_has_the_facts_of_both_with_their_own_cik() -> (
    None
):
    payload = _payload()
    served = {
        1234567: _filed_in_part(payload, 1234567, early=False),
        7654321: _filed_in_part(payload, 7654321, early=True),
    }

    def respond(request: httpx2.Request) -> httpx2.Response:
        for cik, content in served.items():
            if request.url.path == f"/api/xbrl/companyfacts/CIK{cik:010d}.json":
                return httpx2.Response(200, content=content)
        if request.url.path == "/submissions/CIK0001234567.json":
            return httpx2.Response(200, json=SUBMISSIONS)
        return httpx2.Response(404, text="Not Found")

    source = EdgarSource(
        USER_AGENT,
        transport=httpx2.MockTransport(respond),
        min_interval=0.0,
        ciks={"ACME": (1234567, 7654321)},
    )

    facts = source.facts("ACME", date(2024, 12, 31))

    whole = parse_companyfacts(payload, "ACME")
    assert len(facts) == len(whole)
    assert set(facts["cik"]) == {1234567, 7654321}
    assert set(facts[facts["filed"] < "2023-01-01"]["cik"]) == {7654321}
    assert set(facts[facts["filed"] >= "2023-01-01"]["cik"]) == {1234567}
    assert source.security("ACME").cik == 1234567


def test_a_security_is_named_by_the_submissions_of_its_company() -> None:
    requests: list[httpx2.Request] = []

    security = _edgar(requests).security("acme")

    assert security == Security(
        cik=1234567,
        name="Acme Corp",
        sic="3490",
        sic_description="Miscellaneous Fabricated Metal Products",
    )
    assert requests[-1].url.host == "data.sec.gov"
    assert requests[-1].url.path == "/submissions/CIK0001234567.json"


def test_a_company_with_no_submissions_has_no_security() -> None:
    with pytest.raises(NotFoundError, match=r"no facts at .*CIK0001067983"):
        _edgar().security("BRK.B")
    with pytest.raises(NotFoundError, match="no CIK is given for ticker NOPE"):
        _edgar().security("NOPE")
    nameless = EdgarSource(
        USER_AGENT,
        transport=httpx2.MockTransport(
            lambda _: httpx2.Response(200, json={"sic": "1"})
        ),
        ciks=CIKS,
    )
    with pytest.raises(SourceError, match="malformed submissions for ACME"):
        nameless.security("ACME")


def test_a_filing_is_linked_by_its_accession_number() -> None:
    assert (
        filing_url(1234567, "0001234567-23-000006")
        == "https://www.sec.gov/Archives/edgar/data/1234567/"
        "000123456723000006/0001234567-23-000006-index.htm"
    )


def test_edgar_sends_the_contact_user_agent_and_a_padded_cik() -> None:
    requests: list[httpx2.Request] = []

    _edgar(requests).facts("acme", date(2024, 12, 31))

    paths = [request.url.path for request in requests]
    assert paths == ["/api/xbrl/companyfacts/CIK0001234567.json"]
    assert {r.headers["User-Agent"] for r in requests} == {USER_AGENT}


def test_edgar_facts_stop_at_the_end_date() -> None:
    source = _edgar()

    early = source.facts("ACME", date(2023, 6, 1))
    late = source.facts("ACME", date(2024, 12, 31))

    assert early["filed"].max() == pd.Timestamp("2023-06-01")
    assert late["filed"].max() == pd.Timestamp("2024-02-15")


def test_an_unknown_ticker_or_a_company_without_facts_is_not_found() -> None:
    source = _edgar()

    with pytest.raises(NotFoundError, match="no CIK is given for ticker NOPE"):
        source.facts("NOPE", date(2024, 1, 1))
    # The ticker has a CIK, but its facts endpoint is empty.
    with pytest.raises(NotFoundError, match=r"no facts at .*CIK0001067983"):
        source.facts("BRK.B", date(2024, 1, 1))


def test_other_failures_are_plain_source_errors() -> None:
    server_error = EdgarSource(
        USER_AGENT,
        transport=httpx2.MockTransport(lambda _: httpx2.Response(503, text="busy")),
        ciks=CIKS,
    )
    not_json = EdgarSource(
        USER_AGENT,
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, text="<")),
        ciks=CIKS,
    )

    with pytest.raises(SourceError, match="HTTP 503") as raised:
        server_error.facts("ACME", date(2024, 1, 1))
    assert not isinstance(raised.value, NotFoundError)
    with pytest.raises(SourceError, match="invalid JSON"):
        not_json.facts("ACME", date(2024, 1, 1))


def test_edgar_network_failure_is_a_source_error() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no route", request=request)

    source = EdgarSource(USER_AGENT, transport=httpx2.MockTransport(refuse), ciks=CIKS)

    with pytest.raises(SourceError, match="could not reach EDGAR"):
        source.facts("ACME", date(2024, 1, 1))


def test_requests_are_spaced_by_the_minimum_interval() -> None:
    requests: list[httpx2.Request] = []
    times: list[float] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        times.append(time.monotonic())
        requests.append(request)
        return httpx2.Response(200, content=FIXTURE.read_bytes())

    source = EdgarSource(
        USER_AGENT,
        transport=httpx2.MockTransport(respond),
        min_interval=0.2,
        ciks=CIKS,
    )

    source.facts("ACME", date(2024, 1, 1))
    source.facts("ACME", date(2024, 1, 1))

    assert len(times) == 2
    assert all(later - earlier >= 0.19 for earlier, later in pairwise(times))


def test_cached_filings_cost_neither_a_request_nor_a_wait(tmp_path: Path) -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, content=FIXTURE.read_bytes())

    def new_source() -> EdgarSource:
        return EdgarSource(
            USER_AGENT,
            transport=httpx2.MockTransport(respond),
            min_interval=0.5,
            cache=tmp_path,
            ciks=CIKS,
        )

    first = new_source().facts("ACME", date(2024, 12, 31))
    started = time.monotonic()
    again = new_source().facts("ACME", date(2024, 12, 31))

    assert time.monotonic() - started < 0.4
    assert len(requests) == 1
    pd.testing.assert_frame_equal(first, again)


def test_the_user_agent_is_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(SourceError, match="SEC_USER_AGENT is not set"):
        EdgarSource.from_env(None, CIKS)

    monkeypatch.setenv("SEC_USER_AGENT", USER_AGENT)
    assert isinstance(EdgarSource.from_env(None, CIKS), EdgarSource)
