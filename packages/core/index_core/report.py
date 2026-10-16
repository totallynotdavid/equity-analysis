"""The published shapes of a scoring run and of a ticker. `scores.json`, the
ticker files and the API share them."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, computed_field, model_validator


# The model whose scores `eq run` publishes. A verdict about another model says
# nothing about them.
SCORED_MODEL = "lightgbm"


class ScoreRow(BaseModel):
    ticker: str
    rank: int
    score: int
    prob: float


class ModelInfo(BaseModel):
    train_rows: int
    holdout_rows: int
    holdout_auc: float | None


class LeftOut(BaseModel):
    ticker: str
    reason: Literal["no prices", "no filings"]


class ScoresReport(BaseModel):
    status: Literal["experimental, not validated"] = "experimental, not validated"
    as_of: date
    universe: str
    price_source: str
    facts_source: str | None
    horizon_days: int
    model: ModelInfo
    rows: list[ScoreRow]
    left_out: list[LeftOut] = []


class Filing(BaseModel):
    """The filing that reported a figure: its accession number, its form, the
    fiscal year and period it describes, the day it reached the SEC and the
    EDGAR page that lists its documents."""

    accn: str
    form: str
    fy: int
    fp: str
    filed: date
    url: str


class Part(BaseModel):
    """One reported figure a point is made of. `metric` is the quantity it
    reports and `tag` the XBRL tag it was reported under. `start` is empty for a
    balance-sheet value. `sign` is 1 where the figure is added to the point and
    -1 where it is subtracted."""

    metric: str
    sign: Literal[1, -1]
    tag: str
    start: date | None
    end: date
    value: float
    filing: Filing


class Point(BaseModel):
    """The value of a metric for the twelve months to `end`, or on `end` for a
    balance-sheet quantity. `parts` are the filed figures it is made of and
    `formula` says how: `None` where the value is the sum of the signed parts."""

    end: date
    value: float
    formula: str | None = None
    parts: list[Part]


class Series(BaseModel):
    """One metric over the latest periods, oldest first, with where its latest
    value stands in the universe and in the sector, in percent of the group at
    or below it."""

    metric: str
    group: str
    label: str
    unit: str
    pct_universe: float | None
    pct_sector: float | None
    points: list[Point]


class GridRow(BaseModel):
    """The model at one discount rate. `values` is the value of a share at each
    growth of `DcfModel.growths`, never below zero, and `implied_growth` the
    growth that makes the model's value of a share the market price, or None
    where no growth in the solver's range does."""

    wacc: float
    implied_growth: float | None
    values: list[float]


class DcfModel(BaseModel):
    """A two-stage free-cash-flow model solved for the growth the price implies.

    Free cash flow grows at `implied_growth` for `years` years, then at
    `terminal_growth` for ever, discounted at `wacc`. `fcf` is the filed
    trailing free cash flow and `fcf0` the same plus the after-tax interest its
    company paid: the flow that belongs to debt and equity together.
    `enterprise_value` is the market's: market value plus `net_debt`.
    `tv_share` is the part of it the terminal value accounts for at the implied
    growth. `rows` run from the lowest `wacc` to the highest.
    """

    price: float
    shares: float
    market_value: float
    net_debt: float
    enterprise_value: float
    fcf: float
    fcf0: float
    beta_raw: float
    beta: float
    risk_free: float
    equity_risk_premium: float
    cost_of_equity: float
    cost_of_debt: float
    tax_rate: float
    wacc: float
    terminal_growth: float
    years: int
    implied_growth: float
    tv_share: float
    growths: list[float]
    rows: list[GridRow]


class Valuation(BaseModel):
    """What the price of a stock implies, or why that is not meaningful.

    The status is `ok` with a `model`, or `not_meaningful` with a `reason`.
    There is no fair value: the grid of the model is the range.
    """

    status: Literal["ok", "not_meaningful"]
    reason: str | None = None
    model: DcfModel | None = None

    @model_validator(mode="after")
    def _one_outcome(self) -> Valuation:
        ok = self.status == "ok" and self.reason is None and self.model is not None
        refused = (
            self.status == "not_meaningful"
            and self.reason is not None
            and self.model is None
        )
        if not (ok or refused):
            raise ValueError("an ok valuation has a model and no reason, else a reason")
        return self


class Estimate(BaseModel):
    """A regression coefficient, its robust standard error `se` and the 95%
    interval `lo` to `hi` that follows from it."""

    value: float
    se: float
    lo: float
    hi: float


class Loading(Estimate):
    """The coefficient on one Fama-French factor: `mkt_rf`, `smb`, `hml`, `rmw`
    or `cma`."""

    factor: str


class RiskFit(BaseModel):
    """How a stock behaved over the `Risk.window` trading days from `start` to
    `end`, which are the last days with both a return and factor returns.

    `beta` is the slope of its daily return on SPY's. `volatility` is the
    annualised standard deviation of its daily return and `max_drawdown` the
    largest fall from a peak, as a negative fraction. `loadings` are the
    coefficients of its daily return above the risk-free rate on the five
    factors, `alpha` is the annualised intercept of that regression, and `r2` is
    the share of the variance of the return that it explains.
    """

    start: date
    end: date
    beta: Estimate
    volatility: float
    max_drawdown: float
    r2: float
    alpha: Estimate
    loadings: list[Loading]


class Risk(BaseModel):
    """What a stock behaves like, or why that cannot be said.

    The status is `ok` with a `fit`, or `insufficient_history` or
    `not_estimable` with a `reason`. `window` is the number of trading days an
    estimate needs.
    """

    status: Literal["ok", "insufficient_history", "not_estimable"]
    reason: str | None = None
    window: int
    fit: RiskFit | None = None

    @model_validator(mode="after")
    def _one_outcome(self) -> Risk:
        ok = self.status == "ok" and self.reason is None and self.fit is not None
        refused = self.status != "ok" and self.reason is not None and self.fit is None
        if not (ok or refused):
            raise ValueError("an ok risk has a fit and no reason, else a reason")
        return self


class TickerReport(BaseModel):
    """The fundamentals of a ticker as of a date. `facts_source` says where the
    filings came from: `synthetic` ones have no real filing behind them."""

    ticker: str
    universe: str
    as_of: date
    facts_source: str
    name: str
    cik: int
    sic: str
    sic_description: str
    sector: str | None
    series: list[Series]
    valuation: Valuation | None = None
    risk: Risk | None = None


class Interval(BaseModel):
    """A value with its 95% interval. A part is `None` where it could not be
    computed, such as a feature in a run without filings."""

    value: float | None
    lo: float | None
    hi: float | None


class EvidenceRun(BaseModel):
    """What was measured. `period` is `holdout` for the months held back from
    every choice and `development` for the months that chose the candidate.
    `cost_bps` is the assumed one-way cost the net figures use."""

    universe: str
    period: Literal["development", "holdout"]
    holdout_start: date
    first_date: date
    last_date: date
    dates: int
    months: int
    rows: int
    independent_windows: int
    base_rate: float
    horizon_days: int
    cost_bps: float
    members: int
    unpriced: int
    price_source: str
    facts_source: str | None
    factors_source: str | None


class Check(BaseModel):
    name: str
    value: str
    passed: bool


class Verdict(BaseModel):
    """Whether the score may be shown. `decided` is false for development
    months, which choose the candidate and so cannot test it. `shown` is true
    only for a decided verdict whose checks all passed for the model that
    makes the score."""

    candidate: str
    lightgbm_stays: bool
    decided: bool
    checks: list[Check]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def shown(self) -> bool:
        return (
            self.decided
            and self.candidate == SCORED_MODEL
            and all(check.passed for check in self.checks)
        )


class Portfolio(BaseModel):
    """Annualised returns of a monthly portfolio, before and after costs.
    `break_even` is the one-way cost that makes the net return zero, `None`
    where there is no gross edge."""

    gross: Interval
    net: Interval
    sharpe: float | None
    turnover: float
    break_even: Interval | None


class Ladder(BaseModel):
    """The annualised net decile spread if each trade lost `cost_bps`."""

    cost_bps: float
    net: float


class FactorAlpha(BaseModel):
    annual: float
    t_stat: float
    r2: float
    months: int
    loadings: dict[str, float]


class DeflatedSharpe(BaseModel):
    sharpe: float
    benchmark: float
    probability: float
    trials: int


class ModelEvidence(BaseModel):
    """One model on the out-of-sample rows. `top` is the top decile minus every
    scored stock and `spread` the top decile minus the bottom decile.
    `spread_excess` is the mean excess return of score 10 minus score 1 over the
    label horizon. `selection_*` are the figures that chose the candidate."""

    model: str
    mean_ic: Interval
    ic_t_stat: float | None
    mean_auc: Interval
    top_hit_rate: Interval
    spread_excess: Interval
    top: Portfolio
    spread: Portfolio
    ladder: list[Ladder]
    alpha: FactorAlpha | None
    deflated: DeflatedSharpe
    selection_ic: float
    selection_net_spread: float


class YearEvidence(BaseModel):
    """Mean rank IC of each model in one calendar year of the out-of-sample
    rows. A year of under three months has no interval."""

    year: int
    dates: int
    months: int
    ic: dict[str, Interval]


class ScoreEvidence(BaseModel):
    score: int
    rows: int
    hit_rate: Interval
    mean_excess: Interval


class FeatureEvidence(BaseModel):
    feature: str
    family: Literal["technical", "fundamental"]
    mean_ic: Interval
    t_stat: float | None
    dates: int


class Trial(BaseModel):
    description: str
    as_of: date
    months: int
    sharpe: float


class EvidenceReport(BaseModel):
    """The published proof page: the out-of-sample result of a stored backtest
    and the ledger of every specification tried."""

    status: Literal["experimental, not validated"] = "experimental, not validated"
    run: EvidenceRun
    verdict: Verdict
    models: list[ModelEvidence]
    by_year: list[YearEvidence]
    by_score: list[ScoreEvidence]
    features: list[FeatureEvidence]
    feature_hurdle_t: float
    trials: list[Trial]
