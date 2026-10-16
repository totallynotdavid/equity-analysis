"""Every figure of a ticker page against the filing it links to.

The payloads are real `companyfacts` responses recorded from EDGAR (see
`record_companyfacts.py`). The checks read the payload itself, not the code
under test: a figure must be an entry of its filing, with the same tag, period
and value, and a value must be the signed sum of its figures.
"""

import json

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd
import pytest

from index_core.sources.edgar import FORMS, parse_companyfacts
from index_core.statements import statements


if TYPE_CHECKING:
    from datetime import date

    from index_core.report import Part, Point


FIXTURES = Path(__file__).parent / "fixtures"
TICKERS = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "JPM",
    "XOM",
    "WMT",
    "JNJ",
    "PLD",
    "BRK-B",
]
# The metrics every one of the ten has, whatever tags its filings use.
ALWAYS = ["revenue", "net_income", "total_assets", "equity"]
PAYMENT_TAGS = {
    "InterestExpense",
    "InterestExpenseNonoperating",
    "InterestExpenseDebt",
    "InterestAndDebtExpense",
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
}


def _payload(ticker: str) -> dict[str, Any]:
    path = FIXTURES / f"recorded_{ticker.lower()}_companyfacts.json"
    return json.loads(path.read_text())  # type: ignore[no-any-return]


def _filings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        entry
        for tags in payload["facts"].values()
        for tag in tags.values()
        for entries in tag["units"].values()
        for entry in entries
    ]


def _as_of(payload: dict[str, Any]) -> date:
    """The day after the latest filing of the payload."""
    return pd.Timestamp(max(e["filed"] for e in _filings(payload))).date() + (
        pd.Timedelta(days=1)
    )


def _entries(payload: dict[str, Any], tag: str) -> list[dict[str, Any]]:
    return [
        entry
        for taxonomy in payload["facts"].values()
        for entries in taxonomy.get(tag, {}).get("units", {}).values()
        for entry in entries
    ]


def _filed_as(payload: dict[str, Any], part: Part) -> dict[str, Any]:
    """The entry of the filing that `part` points to, or a failure."""
    found = [
        entry
        for entry in _entries(payload, part.tag)
        if entry["accn"] == part.filing.accn
        and entry["end"] == part.end.isoformat()
        and entry.get("start") == (part.start.isoformat() if part.start else None)
    ]
    assert found, f"{part.tag} {part.start}..{part.end} is not in {part.filing.accn}"
    (entry,) = found
    return entry


def _signed(parts: list[Part], metric: str | None = None) -> float:
    return sum(p.sign * p.value for p in parts if metric in (None, p.metric))


def _derived(name: str, parts: list[Part]) -> float:
    """The value of a metric made of several quantities, worked out here."""

    def of(metric: str) -> float:
        return _signed(parts, metric)

    if name == "free_cash_flow":
        return of("operating_cash_flow") - of("capex")
    if name == "net_debt":
        return of("debt") - of("cash")
    top, bottom = {
        "gross_margin": ("gross_profit", "revenue"),
        "op_margin": ("operating_income", "revenue"),
        "net_margin": ("net_income", "revenue"),
        "tax_rate": ("income_tax", "pretax_income"),
    }[name]
    return of(top) / of(bottom)


DERIVED = {"free_cash_flow", "net_debt", "gross_margin", "op_margin", "net_margin"}
DERIVED |= {"tax_rate"}


def _page(ticker: str) -> tuple[dict[str, Any], dict[str, list[Point]], int]:
    payload = _payload(ticker)
    facts = parse_companyfacts(payload, ticker).assign(ticker=ticker)
    cik = int(payload["cik"])
    return payload, statements(facts, _as_of(payload)), cik


@pytest.mark.parametrize("ticker", TICKERS)
def test_every_figure_is_the_figure_of_the_filing_it_links_to(ticker: str) -> None:
    payload, found, cik = _page(ticker)

    assert set(ALWAYS) <= set(found)
    checked = 0
    for name, points in found.items():
        for point in points:
            for part in point.parts:
                entry = _filed_as(payload, part)
                shown = abs(entry["val"]) if part.tag in PAYMENT_TAGS else entry["val"]
                assert part.value == shown, (name, part)
                assert part.filing.form == entry["form"]
                assert part.filing.form in FORMS
                assert (part.filing.fy, part.filing.fp) == (entry["fy"], entry["fp"])
                assert part.filing.filed.isoformat() == entry["filed"]
                assert part.filing.url == (
                    f"https://www.sec.gov/Archives/edgar/data/{cik}/"
                    f"{part.filing.accn.replace('-', '')}/{part.filing.accn}-index.htm"
                )
                checked += 1
    assert checked > 100


@pytest.mark.parametrize("ticker", TICKERS)
def test_every_value_is_the_signed_sum_or_the_formula_of_its_figures(
    ticker: str,
) -> None:
    _, found, _ = _page(ticker)

    for name, points in found.items():
        for point in points:
            if name in DERIVED:
                assert point.formula is not None
                assert point.value == pytest.approx(_derived(name, point.parts))
            else:
                assert point.formula is None
                assert point.value == pytest.approx(_signed(point.parts)), name


@pytest.mark.parametrize("ticker", TICKERS)
def test_a_trailing_twelve_months_adds_up_to_a_year(ticker: str) -> None:
    _, found, _ = _page(ticker)

    for name in ("revenue", "net_income", "operating_income", "pretax_income"):
        for point in found.get(name, []):
            starts = [p for p in point.parts if p.sign > 0 and p.start]
            last_year = [p for p in point.parts if p.sign < 0]
            days = sum((p.end - p.start).days for p in starts if p.start)
            days -= sum((p.end - p.start).days for p in last_year if p.start)
            assert 350 <= days <= 380, (ticker, name, point.end, days)
            assert max(p.end for p in point.parts) == point.end


@pytest.mark.parametrize("ticker", TICKERS)
def test_a_quarterly_balance_is_the_value_on_its_date(ticker: str) -> None:
    payload, found, _ = _page(ticker)

    for point in found["total_assets"]:
        (part,) = point.parts
        assert part.start is None
        assert part.end == point.end
        assert _filed_as(payload, part)["val"] == point.value


def test_the_latest_figures_of_apple_are_those_of_its_latest_annual_report() -> None:
    payload, found, _ = _page("AAPL")

    annual = [
        e
        for e in _entries(
            payload, "RevenueFromContractWithCustomerExcludingAssessedTax"
        )
        if e["form"] == "10-K" and e["fp"] == "FY"
    ]
    last = max(annual, key=lambda e: e["end"])
    at_year_end = next(p for p in found["revenue"] if p.end.isoformat() == last["end"])
    assert at_year_end.value == last["val"]
    assert at_year_end.parts[0].filing.accn == last["accn"]
    assert at_year_end.parts[0].filing.form == "10-K"


def test_a_bank_reports_its_net_revenue_in_every_quarter() -> None:
    payload, found, _ = _page("JPM")
    latest = max(e["end"] for e in _entries(payload, "RevenuesNetOfInterestExpense"))

    point = found["revenue"][-1]

    assert point.end.isoformat() == latest
    tags = {p.tag for p in point.parts}
    assert "RevenuesNetOfInterestExpense" in tags
    assert tags <= {"Revenues", "RevenuesNetOfInterestExpense"}


def test_a_company_that_changed_its_debt_tag_still_has_debt() -> None:
    payload, found, _ = _page("XOM")
    latest = max(
        e["end"] for e in _entries(payload, "LongTermDebtAndCapitalLeaseObligations")
    )

    point = found["debt"][-1]

    assert point.end.isoformat() == latest
    assert {p.tag for p in point.parts} == {
        "LongTermDebtAndCapitalLeaseObligations",
        "DebtCurrent",
    }


@pytest.mark.parametrize("ticker", TICKERS)
def test_a_per_share_figure_has_the_decimals_of_its_filing(ticker: str) -> None:
    _, found, _ = _page(ticker)

    for point in found.get("eps_diluted", []):
        assert point.value == round(point.value, 4), point


def test_a_figure_that_differs_from_its_filing_is_caught() -> None:
    payload, found, _ = _page("MSFT")
    part = found["net_income"][-1].parts[0]
    forged = part.model_copy(update={"value": part.value + 1})

    assert _filed_as(payload, part)["val"] == part.value
    assert _filed_as(payload, forged)["val"] != forged.value
