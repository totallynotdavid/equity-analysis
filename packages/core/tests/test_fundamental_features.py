import json

from datetime import date
from pathlib import Path
from typing import cast

import httpx2
import numpy as np
import pandas as pd
import pytest

from index_core.features.fundamental import (
    FEATURES,
    INPUTS,
    concept_coverage,
    fundamental_features,
    fundamental_inputs,
)
from index_core.sources.edgar import CONCEPTS, parse_companyfacts
from index_core.sources.synthetic import SyntheticFilings
from index_core.sources.tiingo import TiingoSource
from index_core.store import Store


FIXTURE = Path(__file__).parent / "fixtures" / "acme_companyfacts_handwritten.json"
CLOSE = 20.0
_SPLIT_RANGE = (date(2020, 7, 31), date(2020, 9, 1))  # as in test_sources.py


def _facts() -> pd.DataFrame:
    payload = json.loads(FIXTURE.read_text())
    return parse_companyfacts(payload, "ACME").assign(ticker="ACME")


def _prices(
    close: float = CLOSE,
    split_on: str | None = None,
    start: str = "2022-06-01",
    end: str = "2024-03-01",
) -> pd.DataFrame:
    """SPY and ACME on every business day. A 2-for-1 split on `split_on` halves
    the as-traded close and doubles the as-traded volume."""
    dates = pd.bdate_range(start, end, name="date")
    frames = []
    for ticker in ("SPY", "ACME"):
        after = (
            dates >= pd.Timestamp(split_on)
            if split_on and ticker == "ACME"
            else np.zeros(len(dates), dtype=bool)
        )
        frames.append(
            pd.DataFrame(
                {
                    "date": dates,
                    "ticker": ticker,
                    "close": np.where(after, close / 2, close),
                    "volume": np.where(after, 2000.0, 1000.0),
                    "adj_open": close,
                    "adj_high": close,
                    "adj_low": close,
                    "adj_close": close,
                    "adj_volume": 2000.0 if split_on and ticker == "ACME" else 1000.0,
                    "div_cash": 0.0,
                    "split_factor": np.where(after & ~np.roll(after, 1), 2.0, 1.0),
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def _calendar(prices: pd.DataFrame) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(sorted(prices["date"].unique()))


def _inputs_at(day: str, facts: pd.DataFrame | None = None) -> pd.Series:
    inputs = fundamental_inputs(
        _facts() if facts is None else facts, _calendar(_prices())
    )
    return cast("pd.Series", inputs.xs("ACME", level="ticker").loc[day])


def _at(frame: pd.DataFrame | pd.Series, day: str, column: str) -> float:
    return float(cast("pd.Series", frame[column]).loc[day])


def test_a_filing_is_unusable_on_its_filing_day_and_usable_the_next_trading_day() -> (
    None
):
    # The 9M 2023 10-Q was filed on Friday 2023-11-03.
    before = _inputs_at("2023-11-03")
    after = _inputs_at("2023-11-06")

    assert before["net_income"] == 40
    assert after["net_income"] == 48


def test_a_restated_value_is_invisible_until_the_day_after_it_is_filed() -> None:
    # FY2022 revenue was 400 as first filed and 405 in a 10-K/A filed 2023-06-01.
    assert _inputs_at("2023-05-31")["revenue"] == 400
    assert _inputs_at("2023-06-01")["revenue"] == 400
    assert _inputs_at("2023-06-02")["revenue"] == 405


def test_nothing_is_known_before_the_first_filing() -> None:
    # The FY2021 10-K, the first filing, was filed on 2022-02-15.
    inputs = fundamental_inputs(_facts(), _calendar(_prices(start="2022-01-03")))
    acme = inputs.xs("ACME", level="ticker")

    before = acme.loc[:"2022-02-15"]
    assert before.drop(columns=["shares_prev_end", "as_of"]).isna().all().all()
    assert _at(acme, "2022-02-16", "revenue") == 360
    # Nothing a year earlier was filed, so there is no growth base yet.
    assert np.isnan(_at(acme, "2022-02-16", "revenue_prev"))
    # The 9M 2022 10-Q came on 2022-11-04: FY2021 + 9M 2022 - 9M 2021.
    assert acme.loc["2022-11-07", "revenue"] == 360 + 290 - 260


def test_ttm_and_balance_values_match_hand_calculation() -> None:
    inputs = _inputs_at("2023-11-06")

    # Each TTM is the 2022 fiscal year plus nine months of 2023 minus nine
    # months of 2022. Revenue starts from the restated 405.
    assert inputs["revenue"] == 405 + 330 - 290
    assert inputs["gross_profit"] == 160 + 140 - 110
    assert inputs["operating_income"] == 60 + 54 - 40
    assert inputs["net_income"] == 40 + 36 - 28
    assert inputs["depreciation"] == 20 + 16 - 15
    assert inputs["operating_cash_flow"] == 70 + 55 - 45
    assert inputs["capex"] == 25 + 18 - 17
    # The same sum one year earlier, from the 2021 fiscal year.
    assert inputs["revenue_prev"] == 360 + 290 - 260
    assert inputs["net_income_prev"] == 32 + 28 - 22
    assert inputs["total_assets"] == 1000
    assert inputs["total_assets_prev"] == 800
    assert inputs["debt"] == 150 + 50
    assert inputs["equity"] == 500
    assert inputs["cash"] == 100
    assert inputs["shares"] == 60 + 40
    assert inputs["shares_prev"] == 125
    assert inputs["shares_prev_end"] == pd.Timestamp("2022-10-28")
    assert inputs["as_of"] == pd.Timestamp("2023-11-06")


def test_a_full_year_is_its_own_ttm_and_compares_with_the_restated_year() -> None:
    # The FY2023 10-K was filed on 2024-02-15.
    inputs = _inputs_at("2024-02-16")

    assert inputs["revenue"] == 450
    assert inputs["revenue_prev"] == 405


def test_ratios_match_hand_calculation() -> None:
    prices = _prices()
    inputs = fundamental_inputs(_facts(), _calendar(prices))

    features = fundamental_features(inputs, prices, "SPY")

    row = features.xs("ACME", level="ticker").loc["2023-11-06"]
    market_cap = CLOSE * 100
    enterprise = market_cap + 200 - 100
    assert list(features.columns) == list(FEATURES)
    assert row["sales_to_ev"] == pytest.approx(445 / enterprise)
    assert row["ebitda_to_ev"] == pytest.approx((74 + 21) / enterprise)
    assert row["earnings_yield"] == pytest.approx(48 / market_cap)
    assert row["fcf_yield"] == pytest.approx((80 - 26) / market_cap)
    assert row["roa"] == pytest.approx(48 / 1000)
    assert row["gross_margin"] == pytest.approx(190 / 445)
    assert row["op_margin"] == pytest.approx(74 / 445)
    assert row["net_margin"] == pytest.approx(48 / 445)
    assert row["revenue_growth"] == pytest.approx(445 / 390 - 1)
    assert row["earnings_growth"] == pytest.approx((48 - 38) / 38)
    assert row["debt_to_equity"] == pytest.approx(200 / 500)
    assert row["accruals"] == pytest.approx((48 - 80) / 1000)
    assert row["dilution"] == pytest.approx(100 / 125 - 1)
    assert row["asset_growth"] == pytest.approx(1000 / 800 - 1)


def test_a_split_between_filings_does_not_move_the_valuation() -> None:
    prices = _prices(split_on="2023-12-01")
    inputs = fundamental_inputs(_facts(), _calendar(prices))

    features = fundamental_features(inputs, prices, "SPY").xs("ACME", level="ticker")

    # The share count was filed before the split and the close fell by half.
    for column in ("earnings_yield", "fcf_yield", "sales_to_ev"):
        assert features.loc["2023-11-30", column] == pytest.approx(
            features.loc["2023-12-01", column]
        )
    assert features.loc["2023-12-01", "earnings_yield"] == pytest.approx(48 / 2000)


def test_a_split_since_the_year_ago_count_is_not_dilution() -> None:
    # Two-for-one on 2023-01-03: the 125 shares of 2022-10-28 are 250 today, and
    # 100 shares were filed at the 2023-10-27 count, which is a buy-back.
    prices = _prices(split_on="2023-01-03")
    inputs = fundamental_inputs(_facts(), _calendar(prices))

    features = fundamental_features(inputs, prices, "SPY").xs("ACME", level="ticker")

    assert features.loc["2023-11-06", "dilution"] == pytest.approx(100 / 250 - 1)


def test_the_aapl_split_from_a_tiingo_response_keeps_the_valuation(
    tmp_path: Path,
) -> None:
    # Apple's 4-for-1 split on 2020-08-31, from a Tiingo response to the store.
    payload = Path(__file__).parent / "fixtures" / "aapl_prices_handwritten.json"
    source = TiingoSource(
        "key",
        transport=httpx2.MockTransport(
            lambda _: httpx2.Response(200, content=payload.read_text())
        ),
    )
    with Store.open(tmp_path / "db.sqlite") as store:
        store.upsert_prices("tiingo", "AAPL", source.fetch("AAPL", *_SPLIT_RANGE))
        prices = store.read_prices(["AAPL"])

    counted = 4_334_335_000.0  # shares counted on 2020-07-31, before the split
    income = 57.4e9
    rows = pd.DataFrame(
        [
            # Priced before the split with the count of its time.
            ("2020-08-28", "2020-07-31", counted, counted, "2020-07-31"),
            # Priced after the split with the count of the same filing.
            ("2020-09-01", "2020-07-31", counted, counted, "2020-07-31"),
            # Filed after the split, against a count from before it.
            ("2020-09-01", "2020-09-01", 4 * counted, counted, "2020-08-28"),
        ],
        columns=["date", "as_of", "shares", "shares_prev", "shares_prev_end"],
    )
    columns: dict[str, object] = dict.fromkeys(INPUTS, np.nan)
    columns["net_income"] = income
    columns["shares"] = rows["shares"]
    columns["shares_prev"] = rows["shares_prev"]
    columns["as_of"] = pd.to_datetime(rows["as_of"])
    columns["shares_prev_end"] = pd.to_datetime(rows["shares_prev_end"])
    inputs = pd.DataFrame(columns)
    inputs.index = pd.MultiIndex.from_arrays(
        [pd.to_datetime(rows["date"]), ["AAPL"] * 3], names=["date", "ticker"]
    )

    features = fundamental_features(inputs, prices, "SPY")

    before, after, refiled = features["earnings_yield"]
    assert before == pytest.approx(income / (499.23 * counted))
    assert after == pytest.approx(income / (134.18 * 4 * counted))
    assert refiled == pytest.approx(after)
    # A split alone is not dilution.
    assert features["dilution"].tolist() == pytest.approx([0.0, 0.0, 0.0])


def test_negative_denominators_leave_a_ratio_missing_instead_of_wrong() -> None:
    facts = _facts()
    losing = facts.assign(
        value=facts["value"].where(
            ~((facts["concept"] == "equity") | (facts["concept"] == "revenue")),
            -facts["value"],
        )
    )
    prices = _prices()
    inputs = fundamental_inputs(losing, _calendar(prices))

    row = (
        fundamental_features(inputs, prices, "SPY")
        .xs("ACME", level="ticker")
        .loc["2023-11-06"]
    )

    assert np.isnan(row["debt_to_equity"])
    assert np.isnan(row["gross_margin"])
    assert row["earnings_yield"] == pytest.approx(48 / 2000)


def test_a_concept_the_company_stopped_reporting_is_not_carried_for_years() -> None:
    facts = _facts()
    stale = facts[(facts["concept"] != "gross_profit") | (facts["end"] <= "2022-09-30")]
    # Three years on, the company still files revenue but not gross profit.
    later = pd.DataFrame(
        {
            "concept": "revenue",
            "start": pd.Timestamp("2026-01-01"),
            "end": pd.Timestamp("2026-12-31"),
            "filed": pd.Timestamp("2027-02-15"),
            "value": 500.0,
            "priority": 0,
            "ticker": "ACME",
        },
        index=[0],
    )
    calendar = pd.bdate_range("2022-06-01", "2027-03-31")

    inputs = fundamental_inputs(pd.concat([stale, later]), calendar).xs(
        "ACME", level="ticker"
    )

    last = inputs.iloc[-1]
    assert last["revenue"] == 500
    assert np.isnan(last["gross_profit"])


def test_a_company_that_stopped_filing_is_not_current_after_the_lag() -> None:
    # The newest period ends 2023-12-31 and the last filing is on 2024-02-15.
    calendar = _calendar(_prices(end="2026-01-30"))
    inputs = fundamental_inputs(_facts(), calendar).xs("ACME", level="ticker")
    values = [column for column in INPUTS if column not in ("as_of", "shares_prev_end")]

    # Net income ends 2023-09-30, so it is current through 2025-01-02, and
    # revenue ends 2023-12-31, so it is current through 2025-04-04.
    assert _at(inputs, "2025-01-02", "net_income") == pytest.approx(
        _at(inputs, "2024-02-16", "net_income")
    )
    assert np.isnan(_at(inputs, "2025-01-03", "net_income"))
    assert _at(inputs, "2025-04-04", "revenue") > 0
    assert np.isnan(_at(inputs, "2025-04-07", "revenue"))
    assert inputs.loc["2025-04-07":, values].isna().all().all()


def test_a_stale_company_has_no_features_while_a_current_one_keeps_them() -> None:
    stale = _facts()
    current = stale.assign(
        ticker="LIVE",
        end=stale["end"] + pd.Timedelta(days=800),
        start=stale["start"] + pd.Timedelta(days=800),
        filed=stale["filed"] + pd.Timedelta(days=800),
    )
    prices = _prices(end="2026-06-30")
    prices = pd.concat(
        [prices, prices[prices["ticker"] == "ACME"].assign(ticker="LIVE")]
    )
    calendar = _calendar(prices)

    features = fundamental_features(
        fundamental_inputs(pd.concat([stale, current]), calendar), prices, "SPY"
    )

    day = pd.Timestamp("2026-03-02")
    on_day = features.loc[day]
    assert on_day.loc["ACME"].isna().all()
    assert on_day.loc["LIVE"].notna().any()


def test_a_ticker_without_facts_has_no_rows() -> None:
    empty = _facts().iloc[0:0]

    inputs = fundamental_inputs(empty, _calendar(_prices()))

    assert inputs.empty
    assert list(inputs.columns) == list(INPUTS)
    assert inputs.index.names == ["date", "ticker"]


def test_features_at_a_date_do_not_depend_on_later_filings_or_prices(
    prices: pd.DataFrame,
) -> None:
    tickers = [ticker for ticker in sorted(set(prices["ticker"])) if ticker != "SPY"]
    filings = SyntheticFilings()
    facts = pd.concat(
        [filings.facts(t, date(2030, 1, 1)).assign(ticker=t) for t in tickers],
        ignore_index=True,
    )
    calendar = pd.DatetimeIndex(sorted(prices["date"].unique()))
    day = calendar[480]

    garbage = facts.copy()
    later = garbage["filed"] > day
    garbage.loc[later, "value"] = np.random.default_rng(0).uniform(1, 1e12, later.sum())
    garbage_prices = prices.copy()
    after = garbage_prices["date"] > day
    garbage_prices.loc[after, ["close", "volume", "adj_volume"]] = (
        np.random.default_rng(1).uniform(1, 1000, (after.sum(), 3))
    )

    def features_up_to_day(frame: pd.DataFrame, bars: pd.DataFrame) -> pd.DataFrame:
        computed = fundamental_features(
            fundamental_inputs(frame, calendar), bars, "SPY"
        )
        return computed[computed.index.get_level_values("date") <= day]

    expected = features_up_to_day(facts, prices)
    pd.testing.assert_frame_equal(features_up_to_day(garbage, garbage_prices), expected)
    pd.testing.assert_frame_equal(
        features_up_to_day(facts[~later], prices[prices["date"] <= day]), expected
    )

    # The garbage must have reached the features, or the check above is vacuous.
    full = fundamental_features(fundamental_inputs(facts, calendar), prices, "SPY")
    changed = fundamental_features(
        fundamental_inputs(garbage, calendar), garbage_prices, "SPY"
    )
    assert not changed.equals(full)
    assert expected.notna().any().any()


def test_coverage_counts_the_fresh_concepts_of_each_ticker() -> None:
    facts = _facts()
    sparse = facts[facts["concept"].isin(["revenue", "net_income"])].assign(
        ticker="SPARSE"
    )
    both = pd.concat([facts, sparse])

    tickers = ["ACME", "SPARSE", "NOFILINGS"]

    counts = concept_coverage(both, tickers, pd.Timestamp("2023-11-06"))

    assert counts.to_dict() == {"ACME": len(CONCEPTS), "SPARSE": 2, "NOFILINGS": 0}
    # On the filing day the two debt tags, equity and cash are not yet usable.
    earlier = concept_coverage(both, ["ACME"], pd.Timestamp("2023-11-03"))
    assert earlier["ACME"] == len(CONCEPTS) - 4
