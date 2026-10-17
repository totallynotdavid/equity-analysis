"""The daily job: fetch prices, build features, fit, score."""

import hashlib
import json

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

import numpy as np
import pandas as pd

from index_core.baselines import FAMILIES, RIDGE_PENALTY
from index_core.evaluation import Evaluation, evaluate
from index_core.evidence import backtest_record
from index_core.feature_ic import FeatureIC, feature_ics
from index_core.features.fundamental import (
    concept_matrix,
    fundamental_features,
    fundamental_inputs,
    quotes,
    tag_usage,
)
from index_core.features.normalize import rank_by_date
from index_core.features.technical import FEATURES as TECHNICAL
from index_core.features.technical import WARMUP_BARS, technical_features
from index_core.labels import HORIZON, LABEL_SPAN, excess_return_labels, excess_returns
from index_core.metrics import daily_metrics, optional_float
from index_core.model import NUM_ROUNDS, PARAMS, SNAPSHOT_STEP, fit, load_lightgbm
from index_core.portfolio import (
    MIN_PERIODS,
    Holdings,
    Strategy,
    monthly_holdings,
    strategy,
)
from index_core.report import (
    LeftOut,
    ModelInfo,
    Risk,
    ScoreRow,
    ScoresReport,
    Valuation,
)
from index_core.risk import assess, daily_returns
from index_core.scoring import decile_scores
from index_core.significance import Alpha, Deflated, deflated_sharpe, factor_alpha
from index_core.sources.base import NotFoundError, empty_facts
from index_core.statements import ticker_report
from index_core.universe import BENCHMARK
from index_core.valuation import Company, betas, risk_free_rate, valuate
from index_core.verdict import Verdict, decide
from index_core.walkforward import MIN_TRAIN_DATES, MODELS, Fold, walk_forward


if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from index_core.sources.base import FactorSource, FilingsSource, PriceSource
    from index_core.store import Store
    from index_core.universe import Universe

# Backtests reserve these months until `final`.
HOLDOUT_MONTHS = 24
# DEFAULT_COST is an assumed large-cap trading cost.
# No source supplies the bid-ask spreads, so the break-even cost is the measured value.
DEFAULT_COST = 0.001
TRADING_DAYS_PER_MONTH = 21
CALENDAR_PER_TRADING_DAY = 1.5


@dataclass(frozen=True)
class ModelResult:
    """One model on the out-of-sample rows: its ranks and its portfolios."""

    name: str
    evaluation: Evaluation
    # The top decile minus every scored stock, equal-weighted, and the top decile
    # minus the bottom decile.
    top: Strategy
    spread: Strategy
    alpha: Alpha | None
    deflated: Deflated


@dataclass(frozen=True)
class Backtest:
    models: dict[str, ModelResult]
    features: list[FeatureIC]
    verdict: Verdict
    cost: float
    folds: list[Fold]
    price_source: str
    facts_source: str | None
    factors_source: str | None
    period: Literal["development", "holdout"]
    holdout_start: date
    predictions: pd.DataFrame
    members: int
    unpriced: list[str]

    @property
    def evaluation(self) -> Evaluation:
        """The ranking metrics of LightGBM, the model that scores."""
        return self.models["lightgbm"].evaluation


@dataclass(frozen=True)
class Coverage:
    """What the stored filings hold for the members of a universe at `as_of`.

    `present` says per member which concepts have a fresh fact, `concepts`
    counts them, `tags` is `tag_usage` and `no_company` lists the members the
    filings source has no company for.
    """

    as_of: date
    concepts: pd.Series
    present: pd.DataFrame
    tags: pd.DataFrame
    no_company: list[str]
    facts_source: str | None


def ingest(
    source: PriceSource, store: Store, tickers: list[str], start: date, end: date
) -> None:
    """Ask the source for the whole range on every run: adjusted prices change
    after a dividend or split, so stored history can go stale. A source with a
    response cache answers a repeat run inside the cache window from it, so that
    run sees the prices of the first.

    A ticker the source does not know is skipped, because a source can lack a
    delisted company. `missing_prices` lists the skipped tickers. Every label
    needs the benchmark, so a missing `SPY` stops the run.
    """
    store.upsert_prices(source.name, BENCHMARK, source.fetch(BENCHMARK, start, end))
    for ticker in tickers:
        try:
            bars = source.fetch(ticker, start, end)
        except NotFoundError:
            continue
        store.upsert_prices(source.name, ticker, bars)


def ingest_filings(
    source: FilingsSource,
    store: Store,
    tickers: list[str],
    end: date,
    sectors: Mapping[str, str] | None = None,
) -> None:
    """Fetch every company's filed facts and identity again, as filed up to `end`.

    A ticker the source does not know has no facts and no identity, since a
    company can lack filings, and whatever an earlier run stored for it is
    dropped so scoring never uses stale facts. `missing_filings` lists such
    tickers and `eq coverage` reports them. `sectors` gives the current sector
    of a ticker, which the source does not know.
    """
    sectors = sectors or {}
    for ticker in tickers:
        try:
            facts = source.facts(ticker, end)
        except NotFoundError:
            facts = empty_facts()
        store.replace_facts(source.name, ticker, facts)
        try:
            security = source.security(ticker)
        except NotFoundError:
            security = None
        store.replace_security(ticker, security, sectors.get(ticker))


def ingest_factors(source: FactorSource, store: Store) -> None:
    """Replace the stored factor returns with the source's whole file."""
    store.replace_factors(source.name, source.fetch())


def missing_prices(store: Store, tickers: list[str]) -> list[str]:
    priced = store.priced_tickers()
    return [ticker for ticker in tickers if ticker not in priced]


def missing_filings(store: Store, tickers: list[str]) -> list[str]:
    stored = store.filed_tickers()
    return [ticker for ticker in tickers if ticker not in stored]


def _calendar(features: pd.DataFrame) -> pd.DatetimeIndex:
    dates = features.index.get_level_values("date")
    return pd.DatetimeIndex(dates.unique().sort_values())


def ranked_features(
    prices: pd.DataFrame, facts: pd.DataFrame, universe: Universe
) -> pd.DataFrame:
    """Technical and fundamental features of the members, ranked within each date.

    Every price feeds the features, so a name that joins has its history. Only
    the rows of members are kept before the ranks, so a rank compares a stock
    with the other members of its date. A ticker without facts keeps its rows
    with every fundamental missing.
    """
    technical = technical_features(prices, BENCHMARK)
    calendar = _calendar(technical)
    inputs = fundamental_inputs(facts, calendar)
    fundamental = fundamental_features(inputs, prices, BENCHMARK)
    features = technical.join(fundamental)
    return rank_by_date(features[universe.mask(features.index)])


def coverage(store: Store, universe: Universe, as_of: date | None = None) -> Coverage:
    """Concepts each member has fresh facts for on `as_of`, by default the
    latest stored price date."""
    if as_of is None:
        last = store.read_prices([BENCHMARK])["date"].max()
        if pd.isna(last):
            raise ValueError(
                f"no stored prices for {BENCHMARK}; run `eq run` first or pass --as-of"
            )
        day = last
    else:
        day = pd.Timestamp(as_of)
    tickers = universe.members_on(day)
    facts = store.read_facts(tickers)
    # A fact filed on the day is not usable until the next one.
    known = day + pd.Timedelta(days=1)
    present = concept_matrix(facts, tickers, known)
    companies = set(store.read_security(tickers)["ticker"])
    return Coverage(
        as_of=day.date(),
        concepts=present.sum(axis=1).rename("concepts").astype(int),
        present=present,
        tags=tag_usage(facts, tickers, known),
        no_company=[t for t in tickers if t not in companies],
        facts_source=store.facts_source(),
    )


def compute_metrics(store: Store, universe: Universe) -> pd.Timestamp:
    """Compute the daily metrics and the ticker reports of the members, and
    store them in place of the universe's earlier ones. Returns the date they
    are as of: the latest stored `SPY` date.

    A report holds the reverse DCF of its ticker, which reads the prices of the
    members and the stored factor file's risk-free rate, and its risk, which
    reads the prices and the factor file. A member the filings source has no
    company for has no report.
    """
    prices = store.read_prices([BENCHMARK, *universe.tickers])
    days = prices.loc[prices["ticker"] == BENCHMARK, "date"]
    if days.empty:
        raise ValueError(f"no stored prices for {BENCHMARK}; run `eq run` first")
    calendar = pd.DatetimeIndex(days.sort_values().unique())
    as_of = calendar[-1]
    facts = store.read_facts(universe.tickers)
    security = store.read_security(universe.tickers).set_index("ticker")
    sectors = {str(t): str(s) for t, s in security["sector"].dropna().items()}

    inputs = fundamental_inputs(facts, calendar)
    metrics = daily_metrics(inputs, universe, sectors)
    store.replace_metrics(universe.name, metrics)

    percentiles: dict[str, dict[str, tuple[float | None, float | None]]] = {}
    today = metrics[metrics["as_of"] == as_of]
    for ticker, metric, pct_universe, pct_sector in zip(
        today["ticker"],
        today["metric"],
        today["pct_universe"],
        today["pct_sector"],
        strict=True,
    ):
        percentiles.setdefault(ticker, {})[metric] = (
            optional_float(pct_universe),
            optional_float(pct_sector),
        )
    factors = store.read_factors()
    valuations = _valuations(today, inputs, prices, security["sic"], factors, as_of)
    risks = _risks(prices, factors, universe.members_on(as_of), as_of)
    store.replace_risk(universe.name, as_of.date(), risks)

    reports = []
    source = store.facts_source()
    for ticker in universe.members_on(as_of):
        if source is None or ticker not in security.index:
            continue
        report = ticker_report(
            ticker,
            universe.name,
            as_of,
            source,
            cast("dict[str, Any]", security.loc[ticker].to_dict()),
            facts[facts["ticker"] == ticker],
            percentiles.get(ticker, {}),
            valuations.get(ticker),
            risks.get(ticker),
        )
        if report is not None:
            reports.append(report)
    store.replace_ticker_reports(universe.name, reports)
    return as_of


# A ticker whose last bar is older than this many days before the date has no
# price to value.
MAX_PRICE_AGE = pd.Timedelta(days=7)


def _risks(
    prices: pd.DataFrame,
    factors: pd.DataFrame,
    tickers: list[str],
    as_of: pd.Timestamp,
) -> dict[str, Risk]:
    """Assess each ticker with a recent price bar over the available window."""
    returns = daily_returns(
        prices.pivot(index="date", columns="ticker", values="adj_close")
    )
    last_bar = prices.groupby("ticker")["date"].max().to_dict()
    market = returns[BENCHMARK]
    return {
        ticker: assess(returns[ticker], market, factors)
        for ticker in tickers
        if ticker in returns and last_bar[ticker] >= as_of - MAX_PRICE_AGE
    }


def _valuations(
    today: pd.DataFrame,
    inputs: pd.DataFrame,
    prices: pd.DataFrame,
    sics: pd.Series[str],
    factors: pd.DataFrame,
    as_of: pd.Timestamp,
) -> dict[str, Valuation]:
    """The reverse DCF of every ticker with a metric on `as_of`. `today` is
    the `daily_metrics` rows of that day and `sics` the SIC code by ticker."""
    figures = cast(
        "dict[str, dict[str, float]]",
        today.pivot(index="ticker", columns="metric", values="value").to_dict("index"),
    )
    last_bar = prices.groupby("ticker")["date"].max().to_dict()
    latest = inputs[inputs.index.get_level_values("date") == as_of]
    market = quotes(latest, prices).droplevel("date")
    closes = cast("dict[str, float]", market["close"].to_dict())
    shares = cast("dict[str, float]", market["shares"].to_dict())
    beta = cast(
        "dict[str, float]",
        betas(
            prices.pivot(index="date", columns="ticker", values="adj_close"), BENCHMARK
        ).to_dict(),
    )
    rate = np.nan if factors.empty else risk_free_rate(factors)

    result = {}
    for ticker, metrics in figures.items():
        fresh = last_bar.get(ticker, pd.Timestamp(0)) >= as_of - MAX_PRICE_AGE
        result[ticker] = valuate(
            Company(
                sic=str(sics.get(ticker, "")),
                price=closes.get(ticker, np.nan) if fresh else np.nan,
                shares=shares.get(ticker, np.nan),
                free_cash_flow=metrics.get("free_cash_flow", np.nan),
                interest_expense=metrics.get("interest_expense", np.nan),
                tax_rate=metrics.get("tax_rate", np.nan),
                debt=metrics.get("debt", np.nan),
                cash=metrics.get("cash", np.nan),
                beta_raw=beta.get(ticker, np.nan),
                risk_free=np.nan if rate is None else rate,
            )
        )
    return result


def score(store: Store, universe: Universe) -> ScoresReport:
    """Score the members on the latest stored date, fitted on every member-day."""
    prices = store.read_prices([BENCHMARK, *universe.tickers])
    features = ranked_features(prices, store.read_facts(universe.tickers), universe)
    labels = excess_return_labels(prices, BENCHMARK, universe)
    calendar = _calendar(features)

    model = fit(features, labels, calendar)

    as_of = calendar[-1]
    latest = cast("pd.DataFrame", features.xs(as_of, level="date"))
    missing_technical = latest[list(TECHNICAL)].isna().any(axis=1).to_numpy()
    incomplete = sorted(latest.index[missing_technical])
    if incomplete:
        raise ValueError(f"too little history at {as_of.date()} for {incomplete}")

    table = decile_scores(model.predict(latest))
    return ScoresReport(
        as_of=as_of.date(),
        universe=universe.name,
        price_source=store.price_source() or "unknown",
        facts_source=store.facts_source(),
        horizon_days=HORIZON,
        model=ModelInfo(
            train_rows=model.train_rows,
            holdout_rows=model.holdout_rows,
            holdout_auc=model.holdout_auc,
        ),
        rows=[
            ScoreRow(ticker=ticker, rank=rank, score=score, prob=prob)
            for ticker, rank, score, prob in zip(
                table["ticker"],
                table["rank"],
                table["score"],
                table["prob"],
                strict=True,
            )
        ],
    )


def history_needed(holdout_months: int) -> int:
    """Fewest trading days of stored prices a backtest can work with.

    The first fold needs complete features, `MIN_TRAIN_DATES` snapshots and a
    label span between training and test. The development months must then hold
    enough monthly rebalances to judge a portfolio, and a test quarter may start
    up to a quarter late. The held-back months come after them. A final run
    needs the development months too, because they choose its candidate.
    """
    first_snapshot = -(-WARMUP_BARS // SNAPSHOT_STEP) * SNAPSHOT_STEP
    first_fold = first_snapshot + (MIN_TRAIN_DATES - 1) * SNAPSHOT_STEP + LABEL_SPAN
    measured = (MIN_PERIODS + 1) * TRADING_DAYS_PER_MONTH + LABEL_SPAN
    return first_fold + measured + holdout_months * TRADING_DAYS_PER_MONTH


def backtest(
    store: Store,
    universe: Universe,
    first_oos: date | None = None,
    holdout_months: int = HOLDOUT_MONTHS,
    cost: float = DEFAULT_COST,
    *,
    final: bool = False,
) -> Backtest:
    """Walk forward over the stored prices and evaluate the out-of-sample rows.

    A stock is trained on and predicted only on dates when it is a member of
    `universe`. A member with no stored prices is left out and listed in
    `Backtest.unpriced`.
    The last `holdout_months` months stay out of development metrics. With
    `final`, only those months are measured, and the verdict tests the candidate
    that the development months chose.

    Each model's net spread is added to the store's trial ledger, on the
    development months and, with `final`, on the held-out ones, so the deflated
    Sharpe ratio counts every specification ever run on that store. The result
    is stored too, in place of the earlier backtest of the same universe and
    period, for the proof page.
    `cost` is the fraction of each trade's notional lost, one way.
    """
    if cost < 0:
        raise ValueError(f"the cost is {cost}; it cannot be negative")
    load_lightgbm()
    prices = store.read_prices([BENCHMARK, *universe.tickers])
    days = prices.loc[prices["ticker"] == BENCHMARK, "date"]
    if days.empty:
        raise ValueError(f"no stored prices for ['{BENCHMARK}']; run `eq run` first")
    # A name that left before the first stored day has nothing to read.
    required = universe.members_between(days.min(), days.max())
    unpriced = sorted(set(required) - set(prices["ticker"]))
    needed = history_needed(holdout_months)
    if len(days) < needed:
        start = days.max() - pd.Timedelta(days=int(needed * CALENDAR_PER_TRADING_DAY))
        raise ValueError(
            f"{len(days)} days of {BENCHMARK} prices are stored, from "
            f"{days.min().date()}; this backtest needs at least {needed}. "
            f"Run `eq run --start {start.date()}` or earlier"
        )
    features = ranked_features(prices, store.read_facts(universe.tickers), universe)
    excess = excess_returns(prices, BENCHMARK, universe)
    calendar = _calendar(features)

    result = walk_forward(features, excess, calendar, first_oos)
    predictions = result.predictions
    dates = predictions.index.get_level_values("date")
    holdout_start = dates.max() - pd.DateOffset(months=holdout_months)
    in_holdout = dates > holdout_start
    development = predictions[~in_holdout]
    chosen = predictions[in_holdout] if final else development
    for rows, period in ((development, "development"), (chosen, "holdout")):
        if rows.empty:
            raise ValueError(
                f"no {period} predictions; "
                f"the history is too short for {holdout_months} held-back months"
            )

    adj_close = prices.pivot(index="date", columns="ticker", values="adj_close")
    adj_close = adj_close.reindex(adj_close[BENCHMARK].dropna().index)
    factors = store.read_factors()

    def measure(rows: pd.DataFrame, *, holdout: bool) -> dict[str, _Measured]:
        return _measure(
            store, rows, adj_close, universe, first_oos, holdout_months, cost, holdout
        )

    # The candidate is chosen on the development months. A final run measures
    # them too, so that the ledger counts that look.
    selection = measure(development, holdout=False)
    tested = measure(chosen, holdout=True) if final else selection
    trials = store.trial_sharpes()

    def models_of(
        rows: pd.DataFrame, measured: dict[str, _Measured]
    ) -> dict[str, ModelResult]:
        return {
            name: ModelResult(
                name=name,
                evaluation=evaluate(rows, calendar, name),
                top=top,
                spread=spread,
                alpha=None
                if factors.empty
                else factor_alpha(spread.net_returns, holdings, factors),
                deflated=deflated_sharpe(spread.net_returns, trials),
            )
            for name, (holdings, top, spread) in measured.items()
        }

    selection_models = models_of(development, selection)
    models = models_of(chosen, tested) if final else selection_models
    backtested = Backtest(
        models=models,
        features=feature_ics(
            features.reindex(chosen.index).join(chosen["excess"]),
            list(features.columns),
        ),
        verdict=decide(selection_models, models),
        cost=cost,
        folds=result.folds,
        price_source=store.price_source() or "unknown",
        facts_source=store.facts_source(),
        factors_source=store.factors_source(),
        period="holdout" if final else "development",
        holdout_start=holdout_start.date(),
        predictions=chosen,
        members=len(required),
        unpriced=unpriced,
    )
    store.save_backtest(*backtest_record(backtested, universe.name))
    return backtested


type _Measured = tuple[Holdings, Strategy, Strategy]


def _measure(
    store: Store,
    rows: pd.DataFrame,
    adj_close: pd.DataFrame,
    universe: Universe,
    first_oos: date | None,
    holdout_months: int,
    cost: float,
    holdout: bool,
) -> dict[str, _Measured]:
    """The holdings, the top-decile strategy and the decile-spread strategy of
    every model on `rows`. Each model's net spread is added to the ledger."""
    as_of = rows.index.get_level_values("date").max().date()
    measured = {}
    for name in MODELS:
        holdings = monthly_holdings(rows[name], adj_close)
        spread = strategy(
            holdings.top - holdings.bottom,
            holdings.top_traded + holdings.bottom_traded,
            cost,
        )
        if not np.isfinite(spread.monthly_sharpe):
            raise ValueError(f"the {name} net spread has no variation to measure")
        measured[name] = (
            holdings,
            strategy(holdings.top - holdings.universe, holdings.top_traded, cost),
            spread,
        )
        spec = _spec(name, universe, first_oos, holdout_months, cost, final=holdout)
        store.record_trial(
            spec.hash, spec.description, as_of, holdings.periods, spread.monthly_sharpe
        )
    return measured


@dataclass(frozen=True)
class _Spec:
    hash: str
    description: str


def _spec(
    model: str,
    universe: Universe,
    first_oos: date | None,
    holdout_months: int,
    cost: float,
    *,
    final: bool,
) -> _Spec:
    """The identity of a backtest, for the trial ledger.

    It holds everything the author chose: the model and its settings, the
    universe and its membership dates, the dates measured, and the cost.
    """
    settings: dict[str, object] = {
        "lightgbm": {**PARAMS, "rounds": NUM_ROUNDS},
        "ridge": {"penalty": RIDGE_PENALTY},
        "composite": dict(FAMILIES),
    }
    parameters = {
        "model": model,
        "settings": settings[model],
        "universe": universe.name,
        "memberships": [
            [m.ticker, m.start.isoformat(), m.end.isoformat() if m.end else None]
            for m in universe.memberships
        ],
        "first_oos": first_oos.isoformat() if first_oos else None,
        "holdout_months": holdout_months,
        "final": final,
        "cost": cost,
    }
    digest = hashlib.sha256(
        json.dumps(parameters, sort_keys=True).encode()
    ).hexdigest()[:16]
    description = (
        f"{model} on {universe.name}, {'holdout' if final else 'development'}, "
        f"{holdout_months} held-back months, "
        f"first out-of-sample {first_oos or 'as early as allowed'}, "
        f"cost {cost * 1e4:g} bps"
    )
    return _Spec(digest, description)


def run(
    source: PriceSource,
    filings: FilingsSource,
    store: Store,
    universe: Universe,
    start: date,
    end: date,
) -> ScoresReport:
    """Fetch what the members of `start` to `end` need, then score the latest day.

    A name that left before `start` is not fetched. A member with no prices, or
    with no filings, is left out of the whole run and listed in
    `ScoresReport.left_out`.
    """
    load_lightgbm()
    store.require_sources(source.name, filings.name)
    tickers = universe.members_between(start, end)
    ingest(source, store, tickers, start, end)
    ingest_filings(filings, store, tickers, end, universe.sectors)
    unpriced = missing_prices(store, tickers)
    unfiled = [t for t in missing_filings(store, tickers) if t not in unpriced]
    left_out = [
        *(LeftOut(ticker=t, reason="no prices") for t in unpriced),
        *(LeftOut(ticker=t, reason="no filings") for t in unfiled),
    ]
    scored = universe.excluding({item.ticker for item in left_out})
    if not scored.tickers:
        raise ValueError("no ticker has both prices and filings")
    report = score(store, scored).model_copy(update={"left_out": left_out})
    store.save_report(report)
    compute_metrics(store, scored)
    return report
