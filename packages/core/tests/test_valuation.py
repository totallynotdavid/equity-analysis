"""The reverse DCF: its arithmetic against closed forms, its refusals, and the
valuations a pipeline run stores in the ticker reports."""

import itertools
import math

from datetime import date
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
import pytest

from index_core.features.fundamental import INPUTS, quotes
from index_core.pipeline import (
    compute_metrics,
    ingest,
    ingest_factors,
    ingest_filings,
)
from index_core.report import DcfModel, TickerReport, Valuation
from index_core.sources.base import Security
from index_core.sources.edgar import CONCEPTS
from index_core.sources.synthetic import (
    SyntheticFactors,
    SyntheticFilings,
    SyntheticSource,
)
from index_core.store import Store
from index_core.valuation import (
    GROWTH_BOUNDS,
    GROWTHS,
    TERMINAL_GROWTH,
    WACC_STEPS,
    YEARS,
    Company,
    adjusted_beta,
    betas,
    enterprise_value,
    implied_growth,
    risk_free_rate,
    valuate,
)
from membership import always


if TYPE_CHECKING:
    from pathlib import Path


START, END = date(2024, 1, 1), date(2026, 9, 30)


def _company(**changes: Any) -> Company:
    """A profitable industrial with some debt."""
    base = {
        "sic": "3571",
        "price": 50.0,
        "shares": 100.0,
        "free_cash_flow": 400.0,
        "interest_expense": 20.0,
        "tax_rate": 0.2,
        "debt": 500.0,
        "cash": 100.0,
        "beta_raw": 1.2,
        "risk_free": 0.04,
    }
    return Company(**{**base, **changes})


def _model(company: Company) -> DcfModel:
    valuation = valuate(company)
    assert valuation.model is not None, valuation.reason
    return valuation.model


@pytest.mark.parametrize("years", [1, 5, 10, 30])
@pytest.mark.parametrize("growth", [-0.02, 0.0, 0.03])
def test_growth_equal_to_the_terminal_growth_is_the_gordon_formula(
    years: int, growth: float
) -> None:
    fcf0, wacc = 120.0, 0.09

    value = enterprise_value(fcf0, wacc, growth, terminal_growth=growth, years=years)

    assert value.total == pytest.approx(fcf0 * (1 + growth) / (wacc - growth))


def test_a_two_stage_value_matches_the_hand_computed_one() -> None:
    # Flows of 120 and 144 in two years, then 0% for ever from a base of 144:
    #   120 / 1.1 + 144 / 1.1^2 = 109.0909 + 119.0083 = 228.0992
    #   144 / 0.10 / 1.1^2 = 1190.0826
    value = enterprise_value(100.0, 0.10, 0.20, terminal_growth=0.0, years=2)

    assert value.explicit == pytest.approx(120 / 1.1 + 144 / 1.21)
    assert value.explicit == pytest.approx(228.099174, abs=1e-6)
    assert value.terminal == pytest.approx(1190.082645, abs=1e-6)
    assert value.total == pytest.approx(15600 / 11)
    assert value.terminal_share == pytest.approx(1190.082645 / (15600 / 11))


def test_value_falls_as_the_discount_rate_rises() -> None:
    rates = np.linspace(0.04, 0.20, 33)

    totals = [enterprise_value(100.0, rate, 0.08).total for rate in rates]

    assert all(a > b for a, b in itertools.pairwise(totals))


def test_value_rises_with_growth() -> None:
    totals = [enterprise_value(100.0, 0.09, g).total for g in (-0.1, 0.0, 0.1, 0.4)]

    assert totals == sorted(totals)
    assert len(set(totals)) == 4


@pytest.mark.parametrize("wacc", [0.02, 0.025, 0.01, -0.01])
def test_a_terminal_growth_at_or_above_the_discount_rate_is_rejected(
    wacc: float,
) -> None:
    with pytest.raises(ValueError, match="terminal growth"):
        enterprise_value(100.0, wacc, 0.05, terminal_growth=0.025)
    with pytest.raises(ValueError, match="terminal growth"):
        implied_growth(1000.0, 100.0, wacc, terminal_growth=0.025)


def test_the_implied_growth_reproduces_the_target() -> None:
    target = 2500.0

    solved = implied_growth(target, 100.0, 0.09)

    assert solved is not None
    assert enterprise_value(100.0, 0.09, solved).total == pytest.approx(target)


def test_a_target_the_model_cannot_reach_has_no_implied_growth() -> None:
    low, high = GROWTH_BOUNDS
    above = enterprise_value(100.0, 0.09, high).total * 1.01
    below = enterprise_value(100.0, 0.09, low).total * 0.99

    assert implied_growth(above, 100.0, 0.09) is None
    assert implied_growth(below, 100.0, 0.09) is None


def test_a_flow_or_target_that_is_not_positive_is_rejected() -> None:
    with pytest.raises(ValueError, match="positive"):
        implied_growth(1000.0, -5.0, 0.09)
    with pytest.raises(ValueError, match="positive"):
        implied_growth(0.0, 5.0, 0.09)


def test_a_company_with_no_debt_is_discounted_at_its_cost_of_equity() -> None:
    model = _model(_company(debt=0.0, interest_expense=0.0, cash=0.0, beta_raw=1.5))

    beta = 2 / 3 * 1.5 + 1 / 3
    assert model.beta == pytest.approx(beta)
    assert model.cost_of_equity == pytest.approx(
        0.04 + beta * model.equity_risk_premium
    )
    assert model.wacc == pytest.approx(model.cost_of_equity)
    assert model.market_value == 5000.0
    assert model.enterprise_value == 5000.0
    assert model.fcf0 == model.fcf


def test_debt_lowers_the_discount_rate_by_its_after_tax_cost() -> None:
    model = _model(_company())

    # Interest of 20 on debt of 500 is 4%, which is not below the risk-free rate.
    assert model.cost_of_debt == pytest.approx(0.04)
    expected = (5000 * model.cost_of_equity + 500 * 0.04 * (1 - 0.2)) / 5500
    assert model.wacc == pytest.approx(expected)
    assert model.wacc < model.cost_of_equity
    assert model.fcf0 == pytest.approx(400 + 20 * (1 - 0.2))
    assert model.net_debt == 400.0
    assert model.enterprise_value == 5400.0


def test_the_base_row_of_the_grid_is_the_model() -> None:
    model = _model(_company())

    assert model.growths == list(GROWTHS)
    assert [row.wacc - model.wacc for row in model.rows] == pytest.approx(
        list(WACC_STEPS)
    )
    base = model.rows[WACC_STEPS.index(0.0)]
    assert base.wacc == pytest.approx(model.wacc)
    assert base.implied_growth == pytest.approx(model.implied_growth)
    assert model.years == YEARS
    at_implied = enterprise_value(
        model.fcf0, model.wacc, model.implied_growth, model.terminal_growth
    )
    assert at_implied.total == pytest.approx(model.enterprise_value)
    assert at_implied.terminal_share == pytest.approx(model.tv_share)
    assert 0 < model.tv_share < 1


def test_a_grid_value_is_the_equity_per_share_of_its_cell() -> None:
    model = _model(_company())

    row, column = model.rows[3], 2
    total = enterprise_value(
        model.fcf0, row.wacc, model.growths[column], model.terminal_growth
    ).total
    assert row.values[column] == pytest.approx((total - model.net_debt) / model.shares)


def test_grid_values_fall_down_the_discount_rates_and_rise_across_growth() -> None:
    model = _model(_company())

    for column in range(len(model.growths)):
        down = [row.values[column] for row in model.rows]
        assert all(a > b for a, b in itertools.pairwise(down))
    for row in model.rows:
        assert row.values == sorted(row.values)
    implied = [row.implied_growth for row in model.rows]
    assert None not in implied
    assert implied == sorted(implied)  # type: ignore[type-var]


def test_a_cheaper_price_implies_less_growth() -> None:
    cheap = _model(_company(price=30.0))
    dear = _model(_company(price=70.0))

    assert cheap.implied_growth < dear.implied_growth


def test_a_price_far_above_the_cash_flow_still_leaves_a_range() -> None:
    model = _model(_company(price=200.0))

    assert max(row.values[-1] for row in model.rows) > 0
    assert model.market_value == 20000.0


@pytest.mark.parametrize("fcf", [-50.0, 0.0])
def test_a_company_without_positive_free_cash_flow_is_not_meaningful(
    fcf: float,
) -> None:
    valuation = valuate(_company(free_cash_flow=fcf))

    assert valuation.status == "not_meaningful"
    assert valuation.model is None
    assert valuation.reason == "free cash flow is not positive"


@pytest.mark.parametrize("sic", ["6021", "6311", "6798", "6000", "6799"])
def test_a_financial_company_is_not_meaningful(sic: str) -> None:
    valuation = valuate(_company(sic=sic))

    assert valuation.status == "not_meaningful"
    assert valuation.reason is not None
    assert "financial" in valuation.reason


@pytest.mark.parametrize("sic", ["5999", "6800", "7372", ""])
def test_other_industries_are_valued(sic: str) -> None:
    assert valuate(_company(sic=sic)).status == "ok"


def test_what_is_missing_is_named() -> None:
    valuation = valuate(_company(price=np.nan, risk_free=np.nan, beta_raw=np.nan))

    assert valuation.status == "not_meaningful"
    assert valuation.reason == "missing price, beta, risk-free rate"


def test_debt_without_its_interest_cannot_be_priced() -> None:
    valuation = valuate(_company(interest_expense=np.nan))

    assert valuation.reason == "missing interest expense"
    assert valuate(_company(interest_expense=np.nan, debt=0.0)).status == "ok"


def test_a_missing_tax_rate_is_the_statutory_one_and_a_high_one_is_capped() -> None:
    assert _model(_company(tax_rate=np.nan)).tax_rate == 0.21
    assert _model(_company(tax_rate=0.9)).tax_rate == 0.35
    assert _model(_company(tax_rate=-0.3)).tax_rate == 0.0


def test_a_company_worth_less_than_its_cash_is_not_meaningful() -> None:
    valuation = valuate(_company(price=1.0, shares=100.0, cash=900.0, debt=0.0))

    assert valuation.status == "not_meaningful"
    assert valuation.reason == "cash exceeds the market value plus debt"


def test_a_price_that_implies_impossible_growth_is_not_meaningful() -> None:
    low, high = GROWTH_BOUNDS

    for changes in ({"price": 1e6}, {"free_cash_flow": 1e6}):
        valuation = valuate(_company(**changes))
        assert valuation.status == "not_meaningful"
        assert valuation.reason == (
            f"the price implies growth outside {low:.0%} to {high:.0%} a year"
        )


def test_a_discount_rate_near_the_terminal_growth_is_not_meaningful() -> None:
    valuation = valuate(_company(risk_free=0.005, beta_raw=0.0))

    assert valuation.status == "not_meaningful"
    assert valuation.reason is not None
    assert "too near the terminal growth" in valuation.reason


def test_the_terminal_growth_stays_below_the_risk_free_rate() -> None:
    assert _model(_company(risk_free=0.04)).terminal_growth == TERMINAL_GROWTH
    assert _model(_company(risk_free=0.015)).terminal_growth == 0.015


def test_a_valuation_has_a_model_or_a_reason_and_never_both() -> None:
    model = _model(_company())

    with pytest.raises(ValueError, match="ok valuation"):
        Valuation(status="ok")
    with pytest.raises(ValueError, match="ok valuation"):
        Valuation(status="not_meaningful")
    with pytest.raises(ValueError, match="ok valuation"):
        Valuation(status="ok", reason="why", model=model)
    with pytest.raises(ValueError, match="ok valuation"):
        Valuation(status="not_meaningful", reason="why", model=model)


def _closes(market: np.ndarray, **stocks: np.ndarray) -> pd.DataFrame:
    days = pd.bdate_range("2023-01-02", periods=len(market) + 1, name="date")
    columns = {"SPY": market, **stocks}
    return pd.DataFrame(
        {
            name: 100 * np.cumprod(np.concatenate([[1.0], 1 + returns]))
            for name, returns in columns.items()
        },
        index=days,
    )


def test_a_stock_built_from_a_known_beta_recovers_it() -> None:
    rng = np.random.default_rng(7)
    market = rng.normal(0.0004, 0.01, 600)
    noise = rng.normal(0.0, 0.004, 600)
    closes = _closes(
        market, HIGH=1.6 * market + noise, LOW=0.4 * market + noise, COPY=market
    )

    result = betas(closes, "SPY")

    assert result["HIGH"] == pytest.approx(1.6, abs=0.1)
    assert result["LOW"] == pytest.approx(0.4, abs=0.1)
    assert result["COPY"] == pytest.approx(1.0)
    assert "SPY" not in result.index


def test_the_beta_reads_the_latest_two_years_only() -> None:
    rng = np.random.default_rng(3)
    market = rng.normal(0.0, 0.01, 900)
    stock = np.concatenate([2.0 * market[:396], 0.5 * market[396:]])

    result = betas(_closes(market, S=stock), "SPY")

    assert result["S"] == pytest.approx(0.5)


def test_a_stock_with_under_a_year_of_returns_has_no_beta() -> None:
    rng = np.random.default_rng(5)
    market = rng.normal(0.0, 0.01, 400)
    closes = _closes(market, S=market)
    closes.loc[closes.index[:-200], "S"] = np.nan

    assert "S" not in betas(closes, "SPY").index


def test_the_beta_is_shrunk_toward_one() -> None:
    assert adjusted_beta(1.0) == pytest.approx(1.0)
    assert adjusted_beta(0.0) == pytest.approx(1 / 3)
    assert adjusted_beta(2.0) == pytest.approx(5 / 3)


def test_the_risk_free_rate_compounds_the_latest_daily_rates_to_a_year() -> None:
    days = pd.bdate_range("2024-01-01", periods=60)
    factors = pd.DataFrame(
        {"rf": np.r_[np.full(39, 0.001), np.full(21, 0.0001)]}, index=days
    )

    assert risk_free_rate(factors) == pytest.approx(1.0001**252 - 1)
    assert risk_free_rate(factors.iloc[:0]) is None


def test_a_share_count_is_scaled_for_a_split_after_it_was_filed() -> None:
    days = pd.bdate_range("2024-01-01", periods=10, name="date")
    prices = pd.DataFrame(
        {
            "ticker": "ACME",
            "date": days,
            "close": [100.0] * 5 + [50.0] * 5,
            "split_factor": [1.0] * 5 + [2.0] + [1.0] * 4,
        }
    )
    index = pd.MultiIndex.from_product([days[-1:], ["ACME"]], names=["date", "ticker"])
    inputs = pd.DataFrame(np.nan, index=index, columns=list(INPUTS))
    inputs["shares"] = 1000.0
    inputs["as_of"] = days[2]

    quote = quotes(inputs, prices)

    assert quote["close"].iloc[0] == 50.0
    assert quote["shares"].iloc[0] == 2000.0


class _Distressed(SyntheticFilings):
    """Filings of a company that spends far more than its operations bring in."""

    def __init__(self, tickers: set[str]) -> None:
        super().__init__()
        self._tickers = tickers

    def companyfacts(self, ticker: str) -> dict[str, Any]:
        payload = super().companyfacts(ticker)
        if ticker in self._tickers:
            concept = CONCEPTS["capex"]
            tag = payload["facts"][concept.taxonomy][concept.tags[0]]
            for entry in tag["units"][concept.unit]:
                entry["val"] *= 50
        return payload


class _Lender(SyntheticFilings):
    """Filings of a bank, whose SIC code is the commercial banks'."""

    def __init__(self, tickers: set[str]) -> None:
        super().__init__()
        self._tickers = tickers

    def security(self, ticker: str) -> Security:
        found = super().security(ticker)
        if ticker not in self._tickers:
            return found
        return Security(found.cik, found.name, "6022", "State Commercial Banks")


TICKERS = ["AAPL", "AMZN", "GOOGL", "JNJ", "META", "MSFT", "PG", "V"]


def _valued(
    path: Path, filings: SyntheticFilings, *, factors: bool = True
) -> dict[str, TickerReport]:
    with Store.open(path) as store:
        if factors:
            ingest_factors(SyntheticFactors(), store)
        ingest(SyntheticSource(), store, TICKERS, START, END)
        ingest_filings(filings, store, TICKERS, END)
        compute_metrics(store, always(TICKERS, "demo"))
        return {r.ticker: r for r in store.ticker_reports("demo")}


def _valuations(reports: dict[str, TickerReport]) -> dict[str, Valuation]:
    return {t: r.valuation for t, r in reports.items() if r.valuation is not None}


def test_every_report_of_a_run_carries_a_valuation(tmp_path: Path) -> None:
    reports = _valued(tmp_path / "db.sqlite", SyntheticFilings())

    valuations = _valuations(reports)
    assert sorted(valuations) == sorted(TICKERS)
    ok = {t: v.model for t, v in valuations.items() if v.model is not None}
    assert ok
    for model in ok.values():
        assert model.price > 0
        assert model.risk_free == pytest.approx(1.00008**252 - 1)
        rebuilt = enterprise_value(
            model.fcf0, model.wacc, model.implied_growth, model.terminal_growth
        )
        assert rebuilt.total == pytest.approx(model.enterprise_value)
        assert model.enterprise_value == pytest.approx(
            model.market_value + model.net_debt
        )
    for valuation in valuations.values():
        assert valuation.status == ("ok" if valuation.model else "not_meaningful")


def test_a_stored_valuation_reads_back_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite"
    reports = _valued(path, SyntheticFilings())

    with Store.open(path, read_only=True) as store:
        again = store.ticker_report("MSFT")

    assert again == reports["MSFT"]
    assert again is not None
    assert again.valuation is not None


def test_a_negative_free_cash_flow_company_is_not_meaningful(tmp_path: Path) -> None:
    reports = _valued(tmp_path / "db.sqlite", _Distressed({"MSFT", "PG"}))

    valuations = _valuations(reports)
    for ticker in ("MSFT", "PG"):
        assert valuations[ticker].status == "not_meaningful"
        assert valuations[ticker].reason == "free cash flow is not positive"
        fcf = next(s for s in reports[ticker].series if s.metric == "free_cash_flow")
        assert fcf.points[-1].value < 0
    assert any(
        v.status == "ok" for t, v in valuations.items() if t not in ("MSFT", "PG")
    )


def test_a_bank_is_not_meaningful_whatever_its_cash_flow(tmp_path: Path) -> None:
    reports = _valued(tmp_path / "db.sqlite", _Lender({"V"}))

    valuation = _valuations(reports)["V"]
    assert valuation.status == "not_meaningful"
    assert valuation.reason is not None
    assert valuation.reason.startswith("SIC 6022 is a financial company")


def test_without_the_factor_file_there_is_no_risk_free_rate(tmp_path: Path) -> None:
    reports = _valued(tmp_path / "db.sqlite", SyntheticFilings(), factors=False)

    for valuation in _valuations(reports).values():
        assert valuation.status == "not_meaningful"
        assert valuation.reason == "missing risk-free rate"


def test_a_ticker_without_prices_is_not_valued(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite"
    with Store.open(path) as store:
        ingest_factors(SyntheticFactors(), store)
        ingest(SyntheticSource(), store, TICKERS[:-1], START, END)
        ingest_filings(SyntheticFilings(), store, TICKERS, END)
        compute_metrics(store, always(TICKERS, "demo"))
        report = store.ticker_report("V")

    assert report is not None
    assert report.valuation is not None
    assert report.valuation.status == "not_meaningful"
    assert report.valuation.reason is not None
    assert report.valuation.reason.startswith("missing price")


def test_a_price_is_the_close_of_the_date_and_not_the_adjusted_one(
    tmp_path: Path,
) -> None:
    reports = _valued(tmp_path / "db.sqlite", SyntheticFilings())
    bars = SyntheticSource().fetch("MSFT", START, END)

    model = _valuations(reports)["MSFT"].model

    assert model is not None
    assert model.price == pytest.approx(float(bars["close"].iloc[-1]))
    assert math.isfinite(model.beta)
