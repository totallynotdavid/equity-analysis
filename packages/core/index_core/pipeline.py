"""The daily job: fetch prices, build features, fit, score."""

from typing import TYPE_CHECKING

from index_core.features.normalize import rank_by_date
from index_core.features.technical import technical_features
from index_core.labels import HORIZON, excess_return_labels
from index_core.model import fit
from index_core.report import ModelInfo, ScoreRow, ScoresReport
from index_core.scoring import decile_scores
from index_core.universe import BENCHMARK


if TYPE_CHECKING:
    from datetime import date

    from index_core.sources.base import PriceSource
    from index_core.store import Store


def ingest(
    source: PriceSource, store: Store, tickers: list[str], start: date, end: date
) -> None:
    """Fetch the whole range again each time: adjusted prices change after a
    dividend or split, so stored history can go stale."""
    for ticker in [BENCHMARK, *tickers]:
        store.upsert_prices(source.name, ticker, source.fetch(ticker, start, end))


def score(store: Store, universe: str, tickers: list[str], source: str) -> ScoresReport:
    """Fit on the stored prices and score every ticker at the latest date."""
    prices = store.read_prices([BENCHMARK, *tickers])
    features = rank_by_date(technical_features(prices, BENCHMARK))
    labels = excess_return_labels(prices, BENCHMARK)
    calendar = features.index.get_level_values("date").unique().sort_values()

    model = fit(features, labels, calendar)

    as_of = calendar[-1]
    latest = features.xs(as_of, level="date")
    incomplete = sorted(latest.index[latest.isna().any(axis=1)])
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


def run(
    source: PriceSource,
    store: Store,
    universe: str,
    tickers: list[str],
    start: date,
    end: date,
) -> ScoresReport:
    ingest(source, store, tickers, start, end)
    report = score(store, universe, tickers, source.name)
    store.save_report(report)
    return report
