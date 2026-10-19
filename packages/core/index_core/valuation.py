"""What a stock's price implies about its growth: a reverse DCF.

A two-stage model discounts the free cash flow of a company that grows at `g` for
`YEARS` years and at the terminal growth after that. `implied_growth` finds the
`g` that makes the model's value the market's enterprise value. `valuate` does
that for one company, repeats it across a range of discount rates, and tabulates
the model's value over a range of growth. It gives no fair value: a DCF is a
function of its assumptions, and the grid shows how much.

The discount rate is a weighted average of a cost of equity from CAPM and an
after-tax cost of debt. The beta is our own regression against the benchmark.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from index_core.report import DcfModel, GridRow, Valuation


YEARS = 10
# The growth after the explicit years, held below the risk-free rate.
TERMINAL_GROWTH = 0.025
# No source in this project supplies an equity risk premium, so this is an
# assumption. The grid spans the discount rates around the one it gives.
EQUITY_RISK_PREMIUM = 0.05
# Used for a company that reports no income tax rate. It is the US federal rate.
STATUTORY_TAX_RATE = 0.21
MAX_TAX_RATE = 0.35
# The implied growth is searched for within these annual rates. A price outside
# the model's value at both ends is not meaningful.
GROWTH_BOUNDS = (-0.5, 1.0)
WACC_STEPS = (-0.02, -0.01, 0.0, 0.01, 0.02)
GROWTHS = (-0.05, 0.0, 0.05, 0.10, 0.15, 0.20, 0.25)
BETA_RETURNS = 504
MIN_BETA_RETURNS = 252
RISK_FREE_DAYS = 21
TRADING_DAYS = 252
# SIC divisions H (finance, insurance and real estate) have no free cash flow
# that is separate from their financing: interest is their revenue and a REIT
# distributes its income.
FINANCIAL_SIC = range(6000, 6800)


@dataclass(frozen=True)
class Value:
    """The enterprise value of a model, in the present values of its two parts."""

    explicit: float
    terminal: float

    @property
    def total(self) -> float:
        return self.explicit + self.terminal

    @property
    def terminal_share(self) -> float:
        return self.terminal / self.total


def enterprise_value(
    fcf0: float,
    wacc: float,
    growth: float,
    terminal_growth: float = TERMINAL_GROWTH,
    years: int = YEARS,
) -> Value:
    """The present value of `fcf0` growing at `growth` for `years` years, then at
    `terminal_growth` for ever, discounted at `wacc`. The first flow is one year
    out. Raises `ValueError` where `terminal_growth` is not below `wacc`, because
    the terminal value is then infinite or negative.
    """
    if terminal_growth >= wacc:
        raise ValueError(
            f"terminal growth {terminal_growth} must be below the discount rate {wacc}"
        )
    if years < 1:
        raise ValueError(f"years must be at least 1, not {years}")
    periods = np.arange(1, years + 1)
    flows = fcf0 * (1 + growth) ** periods
    explicit = float(np.sum(flows / (1 + wacc) ** periods))
    terminal = float(
        flows[-1]
        * (1 + terminal_growth)
        / (wacc - terminal_growth)
        / (1 + wacc) ** years
    )
    return Value(explicit, terminal)


def implied_growth(
    target: float,
    fcf0: float,
    wacc: float,
    terminal_growth: float = TERMINAL_GROWTH,
    years: int = YEARS,
    bounds: tuple[float, float] = GROWTH_BOUNDS,
) -> float | None:
    """The growth at which the model is worth `target`, or None where `target`
    is outside what the model is worth at the ends of `bounds`.

    The value rises with growth while `fcf0` is positive, so bisection finds the
    one answer. Raises `ValueError` for a `fcf0` or `target` that is not positive.
    """
    if fcf0 <= 0 or target <= 0:
        raise ValueError("free cash flow and target value must be positive")
    low, high = bounds

    def gap(growth: float) -> float:
        return (
            enterprise_value(fcf0, wacc, growth, terminal_growth, years).total - target
        )

    if gap(low) > 0 or gap(high) < 0:
        return None
    for _ in range(200):
        middle = (low + high) / 2
        if gap(middle) < 0:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def betas(adj_close: pd.DataFrame, benchmark: str) -> pd.Series:
    """The regression slope of each column's daily returns on the benchmark's,
    over the last `BETA_RETURNS` days both have a return. A column with fewer
    than `MIN_BETA_RETURNS` has none.

    `adj_close` has a column per ticker, the benchmark among them, and a
    sorted date index.
    """
    returns = (adj_close / adj_close.shift(1) - 1).iloc[1:]
    market = returns[benchmark]
    result: dict[str, float] = {}
    for ticker in returns.columns:
        pair = returns[[ticker]].join(market.rename("market"), how="inner").dropna()
        pair = pair.iloc[-BETA_RETURNS:]
        if ticker == benchmark or len(pair) < MIN_BETA_RETURNS:
            continue
        variance = pair["market"].var()
        if variance > 0:
            result[str(ticker)] = float(pair[ticker].cov(pair["market"]) / variance)
    return pd.Series(result, dtype=float)


def adjusted_beta(raw: float) -> float:
    """Blume's adjustment: a regression beta is noisy, and betas drift toward 1."""
    return 2 / 3 * raw + 1 / 3


def risk_free_rate(factors: pd.DataFrame) -> float | None:
    """The annual risk-free rate: the last `RISK_FREE_DAYS` days of the daily
    `rf` of the factor file compounded to a year. None before any are stored."""
    daily = factors["rf"].dropna().iloc[-RISK_FREE_DAYS:]
    if daily.empty:
        return None
    return float(np.prod(1 + daily.to_numpy()) ** (TRADING_DAYS / len(daily)) - 1)


@dataclass(frozen=True)
class Company:
    """What `valuate` reads of a company, as the metrics and prices hold it. A
    quantity that is not known is NaN. `shares` are corrected for splits since
    they were filed, and `interest_expense` and `debt` are positive amounts."""

    sic: str
    price: float
    shares: float
    free_cash_flow: float
    interest_expense: float
    tax_rate: float
    debt: float
    cash: float
    beta_raw: float
    risk_free: float


def not_meaningful(reason: str) -> Valuation:
    return Valuation(status="not_meaningful", reason=reason)


def valuate(company: Company) -> Valuation:
    """The reverse DCF of a company, or the reason there is none.

    The flow is trailing free cash flow plus the after-tax interest the company
    paid, so it is the flow before financing and a weighted cost of capital
    applies. The market's enterprise value is its market value plus debt less
    cash.
    """
    if company.sic.isdigit() and int(company.sic) in FINANCIAL_SIC:
        return not_meaningful(
            f"SIC {company.sic} is a financial company: its free cash flow "
            "does not separate operations from financing"
        )
    known = {
        "price": company.price,
        "shares": company.shares,
        "free cash flow": company.free_cash_flow,
        "debt": company.debt,
        "cash": company.cash,
        "beta": company.beta_raw,
        "risk-free rate": company.risk_free,
    }
    missing = [name for name, value in known.items() if np.isnan(value)]
    if missing:
        return not_meaningful(f"missing {', '.join(missing)}")
    fcf, debt, cash = company.free_cash_flow, company.debt, company.cash
    if fcf <= 0:
        return not_meaningful("free cash flow is not positive")
    if debt > 0 and np.isnan(company.interest_expense):
        return not_meaningful("missing interest expense")

    tax = STATUTORY_TAX_RATE if np.isnan(company.tax_rate) else company.tax_rate
    tax = min(max(tax, 0.0), MAX_TAX_RATE)
    interest = 0.0 if np.isnan(company.interest_expense) else company.interest_expense
    price, shares, risk_free = company.price, company.shares, company.risk_free
    market_value = price * shares
    net_debt = debt - cash
    market_ev = market_value + net_debt
    if market_ev <= 0:
        return not_meaningful("cash exceeds the market value plus debt")

    beta = adjusted_beta(company.beta_raw)
    cost_of_equity = risk_free + beta * EQUITY_RISK_PREMIUM
    cost_of_debt = (
        min(max(interest / debt, risk_free), cost_of_equity) if debt > 0 else risk_free
    )
    capital = market_value + debt
    wacc = (market_value * cost_of_equity + debt * cost_of_debt * (1 - tax)) / capital
    terminal_growth = min(TERMINAL_GROWTH, risk_free)
    if wacc + min(WACC_STEPS) <= terminal_growth:
        return not_meaningful(
            f"the discount rate {wacc:.2%} is too near the terminal growth "
            f"{terminal_growth:.2%} for the grid"
        )

    fcf0 = fcf + interest * (1 - tax)
    solved = implied_growth(market_ev, fcf0, wacc, terminal_growth)
    if solved is None:
        low, high = GROWTH_BOUNDS
        return not_meaningful(
            f"the price implies growth outside {low:.0%} to {high:.0%} a year"
        )
    rows = []
    for step in WACC_STEPS:
        rate = wacc + step
        rows.append(
            GridRow(
                wacc=rate,
                implied_growth=implied_growth(market_ev, fcf0, rate, terminal_growth),
                values=[
                    max(
                        0.0,
                        (
                            enterprise_value(fcf0, rate, growth, terminal_growth).total
                            - net_debt
                        )
                        / shares,
                    )
                    for growth in GROWTHS
                ],
            )
        )
    return Valuation(
        status="ok",
        model=DcfModel(
            price=price,
            shares=shares,
            market_value=market_value,
            net_debt=net_debt,
            enterprise_value=market_ev,
            fcf=fcf,
            fcf0=fcf0,
            beta_raw=company.beta_raw,
            beta=beta,
            risk_free=risk_free,
            equity_risk_premium=EQUITY_RISK_PREMIUM,
            cost_of_equity=cost_of_equity,
            cost_of_debt=cost_of_debt,
            tax_rate=tax,
            wacc=wacc,
            terminal_growth=terminal_growth,
            years=YEARS,
            implied_growth=solved,
            tv_share=enterprise_value(
                fcf0, wacc, solved, terminal_growth
            ).terminal_share,
            growths=list(GROWTHS),
            rows=rows,
        ),
    )
