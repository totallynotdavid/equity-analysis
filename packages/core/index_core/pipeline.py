"""The daily job: fetch prices, build features, fit, score."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import pandas as pd

from index_core.evaluation import Evaluation, evaluate
from index_core.features.fundamental import (
    concept_coverage,
    fundamental_features,
    fundamental_inputs,
)
from index_core.features.normalize import rank_by_date
from index_core.features.technical import FEATURES as TECHNICAL
from index_core.features.technical import technical_features
from index_core.labels import HORIZON, excess_return_labels, excess_returns
from index_core.model import fit
from index_core.report import ModelInfo, ScoreRow, ScoresReport
from index_core.scoring import decile_scores
from index_core.sources.base import NotFoundError, empty_facts
from index_core.universe import BENCHMARK
from index_core.walkforward import Fold, walk_forward


if TYPE_CHECKING:
    from datetime import date

    from index_core.sources.base import FilingsSource, PriceSource
    from index_core.store import Store

# Backtests reserve these months until `final`.
HOLDOUT_MONTHS = 24


@dataclass(frozen=True)
class Backtest:
    evaluation: Evaluation
    folds: list[Fold]
    price_source: str
    facts_source: str | None
    period: Literal["development", "holdout"]
    holdout_start: date


@dataclass(frozen=True)
class Coverage:
    as_of: date
    concepts: pd.Series
    facts_source: str | None


def ingest(
    source: PriceSource, store: Store, tickers: list[str], start: date, end: date
) -> None:
    """Fetch the whole range again each time: adjusted prices change after a
    dividend or split, so stored history can go stale."""
    for ticker in [BENCHMARK, *tickers]:
        store.upsert_prices(source.name, ticker, source.fetch(ticker, start, end))


def ingest_filings(
    source: FilingsSource, store: Store, tickers: list[str], end: date
) -> None:
    """Fetch every company's filed facts again, as filed up to `end`.

    A ticker the source does not know has no facts, since a company can lack
    filings, and whatever an earlier run stored for it is dropped so scoring
    never uses stale facts. `missing_filings` lists such tickers and
    `eq coverage` reports them.
    """
    for ticker in tickers:
        try:
            facts = source.facts(ticker, end)
        except NotFoundError:
            facts = empty_facts()
        store.replace_facts(source.name, ticker, facts)


def missing_filings(store: Store, tickers: list[str]) -> list[str]:
    stored = set(store.read_facts(tickers)["ticker"])
    return [ticker for ticker in tickers if ticker not in stored]


def ranked_features(prices: pd.DataFrame, facts: pd.DataFrame) -> pd.DataFrame:
    """Technical and fundamental features ranked within each date.

    A ticker without facts keeps its rows with every fundamental missing.
    """
    technical = technical_features(prices, BENCHMARK)
    calendar = technical.index.get_level_values("date").unique().sort_values()
    inputs = fundamental_inputs(facts, calendar)
    fundamental = fundamental_features(inputs, prices, BENCHMARK)
    return rank_by_date(technical.join(fundamental))


def coverage(store: Store, tickers: list[str]) -> Coverage:
    """Concepts each ticker has fresh facts for at the latest stored price date."""
    last = store.read_prices([BENCHMARK])["date"].max()
    if pd.isna(last):
        raise ValueError(f"no stored prices for {BENCHMARK}; run `eq run` first")
    # A fact filed on the last day is not usable until the next one.
    counts = concept_coverage(
        store.read_facts(tickers), tickers, last + pd.Timedelta(days=1)
    )
    return Coverage(
        as_of=last.date(), concepts=counts, facts_source=store.facts_source()
    )


def score(store: Store, universe: str, tickers: list[str], source: str) -> ScoresReport:
    prices = store.read_prices([BENCHMARK, *tickers])
    features = ranked_features(prices, store.read_facts(tickers))
    labels = excess_return_labels(prices, BENCHMARK)
    calendar = features.index.get_level_values("date").unique().sort_values()

    model = fit(features, labels, calendar)

    as_of = calendar[-1]
    latest = features.xs(as_of, level="date")
    incomplete = sorted(latest.index[latest[list(TECHNICAL)].isna().any(axis=1)])
    if incomplete:
        raise ValueError(f"too little history at {as_of.date()} for {incomplete}")

    table = decile_scores(model.predict(latest))
    return ScoresReport(
        as_of=as_of.date(),
        universe=universe,
        source=source,
        horizon_days=HORIZON,
        model=ModelInfo(
            train_rows=model.train_rows,
            holdout_rows=model.holdout_rows,
            holdout_auc=model.holdout_auc,
        ),
        rows=[
            ScoreRow(ticker=row.ticker, rank=row.rank, score=row.score, prob=row.prob)
            for row in table.itertuples()
        ],
    )


def backtest(
    store: Store,
    tickers: list[str],
    first_oos: date | None = None,
    holdout_months: int = HOLDOUT_MONTHS,
    *,
    final: bool = False,
) -> Backtest:
    """Walk forward over the stored prices and evaluate the out-of-sample rows.

    The last `holdout_months` months stay out of development metrics. With
    `final`, only those months are measured.
    """
    prices = store.read_prices([BENCHMARK, *tickers])
    missing = sorted({BENCHMARK, *tickers} - set(prices["ticker"]))
    if missing:
        raise ValueError(f"no stored prices for {missing}; run `eq run` first")
    features = ranked_features(prices, store.read_facts(tickers))
    excess = excess_returns(prices, BENCHMARK)
    calendar = features.index.get_level_values("date").unique().sort_values()

    result = walk_forward(features, excess, calendar, first_oos)
    predictions = result.predictions
    dates = predictions.index.get_level_values("date")
    holdout_start = dates.max() - pd.DateOffset(months=holdout_months)
    in_holdout = dates > holdout_start
    chosen = predictions[in_holdout if final else ~in_holdout]
    if chosen.empty:
        raise ValueError(
            f"no {'holdout' if final else 'development'} predictions; "
            f"the history is too short for {holdout_months} held-back months"
        )
    return Backtest(
        evaluation=evaluate(chosen, calendar),
        folds=result.folds,
        price_source=store.price_source() or "unknown",
        facts_source=store.facts_source(),
        period="holdout" if final else "development",
        holdout_start=holdout_start.date(),
    )


def run(
    source: PriceSource,
    filings: FilingsSource,
    store: Store,
    universe: str,
    tickers: list[str],
    start: date,
    end: date,
) -> ScoresReport:
    ingest(source, store, tickers, start, end)
    ingest_filings(filings, store, tickers, end)
    report = score(store, universe, tickers, source.name)
    store.save_report(report)
    return report
