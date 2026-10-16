"""The figures a ticker page shows, against the hand-written filings of Acme.

Every expected number is worked out from `acme_companyfacts_handwritten.json`:
a trailing twelve months is the last annual figure plus the year to date minus
the year to date a year earlier.
"""

import json

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from index_core.sources.edgar import parse_companyfacts
from index_core.statements import statements, ticker_report


if TYPE_CHECKING:
    import pandas as pd

    from index_core.report import Point


FIXTURE = Path(__file__).parent / "fixtures" / "acme_companyfacts_handwritten.json"
CIK = 1234567
AFTER_Q3 = date(2023, 11, 10)
AFTER_FY = date(2024, 3, 1)


def _facts() -> pd.DataFrame:
    return parse_companyfacts(json.loads(FIXTURE.read_text()), "ACME")


def _at(points: list[Point], end: date) -> Point:
    (found,) = (p for p in points if p.end == end)
    return found


def test_a_trailing_twelve_months_is_the_year_plus_the_year_to_date_less_the_last() -> (
    None
):
    found = statements(_facts(), AFTER_Q3)

    net = _at(found["net_income"], date(2023, 9, 30))
    assert net.value == 40 + 36 - 28
    assert sorted((p.sign, p.value, p.filing.form) for p in net.parts) == [
        (-1, 28, "10-Q"),
        (1, 36, "10-Q"),
        (1, 40, "10-K"),
    ]
    assert _at(found["pretax_income"], date(2023, 9, 30)).value == 54 + 48 - 36
    assert _at(found["income_tax"], date(2023, 9, 30)).value == 14 + 12 - 8
    assert _at(found["stock_compensation"], date(2023, 9, 30)).value == 4 + 5 - 3
    assert _at(found["interest_expense"], date(2023, 9, 30)).value == 8 + 7 - 6


def test_a_full_year_is_its_own_trailing_twelve_months() -> None:
    found = statements(_facts(), AFTER_FY)

    revenue = _at(found["revenue"], date(2023, 12, 31))
    assert revenue.value == 450
    assert [p.filing.accn for p in revenue.parts] == ["0001234567-24-000007"]


def test_a_restated_period_shows_the_latest_value_and_the_filing_that_first_gave_it() -> (
    None
):
    before = statements(_facts(), date(2023, 5, 1))
    after = statements(_facts(), date(2023, 7, 1))

    assert _at(before["revenue"], date(2022, 12, 31)).value == 400
    restated = _at(after["revenue"], date(2022, 12, 31))
    assert restated.value == 405
    assert restated.parts[0].filing.form == "10-K/A"
    assert restated.parts[0].filing.accn == "0001234567-23-000020"


def test_a_figure_names_the_tag_that_carried_it() -> None:
    found = statements(_facts(), AFTER_Q3)

    revenue = _at(found["revenue"], date(2023, 9, 30))
    tags = {(p.end, p.tag) for p in revenue.parts}
    assert (
        date(2023, 9, 30),
        "RevenueFromContractWithCustomerExcludingAssessedTax",
    ) in tags
    assert (date(2022, 9, 30), "Revenues") in tags
    assert revenue.value == 405 + 330 - 290


def test_a_figure_links_to_the_first_filing_that_reported_its_value() -> None:
    found = statements(_facts(), AFTER_Q3)

    year_ago = _at(found["revenue"], date(2023, 9, 30)).parts[-1]
    assert (year_ago.value, year_ago.sign) == (290, -1)
    assert year_ago.filing.accn == "0001234567-22-000030"
    assert year_ago.filing.filed == date(2022, 11, 4)
    assert year_ago.filing.url == (
        "https://www.sec.gov/Archives/edgar/data/1234567/"
        "000123456722000030/0001234567-22-000030-index.htm"
    )


def test_a_figure_links_to_the_filing_under_the_registrant_that_filed_it() -> None:
    facts = _facts()
    facts.loc[facts["filed"] < "2023-01-01", "cik"] = 7654321

    found = statements(facts, AFTER_Q3)

    urls = {part.filing.url for point in found["revenue"] for part in point.parts}
    assert any("/data/7654321/" in url for url in urls)
    assert any("/data/1234567/" in url for url in urls)


def test_nothing_filed_on_or_after_the_date_is_used() -> None:
    found = statements(_facts(), date(2023, 11, 3))

    assert max(p.end for p in found["net_income"]) == date(2022, 12, 31)


def test_a_balance_sheet_quantity_is_the_filed_value_on_its_date() -> None:
    found = statements(_facts(), AFTER_Q3)

    assets = _at(found["total_assets"], date(2023, 9, 30))
    assert assets.value == 1000
    assert assets.parts[0].start is None
    debt = _at(found["debt"], date(2023, 9, 30))
    assert debt.value == 150 + 50
    assert {p.metric for p in debt.parts} == {"debt"}
    assert _at(found["net_debt"], date(2023, 9, 30)).value == 200 - 100


def test_diluted_shares_are_the_longest_period_ending_on_the_date() -> None:
    found = statements(_facts(), AFTER_Q3)

    shares = _at(found["diluted_shares"], date(2023, 9, 30))
    assert shares.value == 105
    assert shares.parts[0].start == date(2023, 1, 1)


def test_a_ratio_shows_its_formula_and_the_parts_of_both_terms() -> None:
    found = statements(_facts(), AFTER_Q3)

    margin = _at(found["gross_margin"], date(2022, 12, 31))
    assert margin.formula == "gross profit / revenue"
    assert margin.value == pytest.approx(160 / 405)
    assert {p.metric for p in margin.parts} == {"gross_profit", "revenue"}


def test_at_most_the_asked_number_of_points_is_returned_oldest_first() -> None:
    found = statements(_facts(), AFTER_FY, points=2)

    ends = [p.end for p in found["net_income"]]
    assert ends == sorted(ends)
    assert len(ends) == 2


def test_the_report_of_a_ticker_carries_its_security_and_percentiles() -> None:
    security = {
        "cik": CIK,
        "name": "Acme Corp",
        "sic": "3490",
        "sic_description": "Metal",
        "sector": "Industrials",
    }

    report = ticker_report(
        "ACME",
        "demo",
        AFTER_Q3,
        "edgar",
        security,
        _facts(),
        {"net_income": (80.0, None)},
    )

    assert report is not None
    assert (report.name, report.sector, report.as_of, report.facts_source) == (
        "Acme Corp",
        "Industrials",
        AFTER_Q3,
        "edgar",
    )
    series = {s.metric: s for s in report.series}
    assert (series["net_income"].pct_universe, series["net_income"].pct_sector) == (
        80.0,
        None,
    )
    assert series["revenue"].group == "Income statement"
    assert series["eps_diluted"].unit == "USD/share"


def test_a_ticker_with_no_filings_before_the_date_has_no_report() -> None:
    security = {"cik": CIK, "name": "A", "sic": "1", "sic_description": "x"}

    assert (
        ticker_report(
            "ACME",
            "demo",
            date(2020, 1, 1),
            "edgar",
            {**security, "sector": float("nan")},
            _facts(),
            {},
        )
        is None
    )
