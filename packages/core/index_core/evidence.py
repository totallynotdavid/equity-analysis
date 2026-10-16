"""The proof page's numbers: what a backtest stores and what the page reads back.

`backtest_record` flattens a `Backtest` into the figures `Store.save_backtest`
keeps. `build_evidence` reads them again, with the stored out-of-sample rows and
the trial ledger, into the `EvidenceReport` that `eq export` publishes. Nothing
on the page is typed in: a figure is either stored or computed here from stored
rows.
"""

import math

from typing import TYPE_CHECKING

import pandas as pd

from index_core.backtest_text import COST_LADDER_BPS
from index_core.evaluation import MIN_NAMES, mean_by_month_block
from index_core.feature_ic import HURDLE_T, family_of
from index_core.labels import HORIZON
from index_core.report import Check as CheckShape
from index_core.report import (
    DeflatedSharpe,
    EvidenceReport,
    EvidenceRun,
    FactorAlpha,
    FeatureEvidence,
    Interval,
    Ladder,
    ModelEvidence,
    Portfolio,
    ScoreEvidence,
    Verdict,
    YearEvidence,
)
from index_core.store import EvalRow
from index_core.walkforward import MODELS


if TYPE_CHECKING:
    from collections.abc import Iterable

    from index_core.evaluation import Estimate
    from index_core.pipeline import Backtest
    from index_core.portfolio import Strategy
    from index_core.store import Store, StoredBacktest

BPS = 1e4
# A year of fewer months has too few blocks for an interval.
MIN_YEAR_MONTHS = 3


def _number(value: float | None) -> float | None:
    """`value`, or None where it is missing: SQLite and JSON hold no NaN."""
    return None if value is None or math.isnan(value) else float(value)


def _estimate(
    model: str,
    metric: str,
    estimate: Estimate,
    key: str = "",
    net_of_cost_bps: float | None = None,
) -> EvalRow:
    return EvalRow(
        model,
        metric,
        key,
        _number(estimate.value),
        _number(estimate.low),
        _number(estimate.high),
        net_of_cost_bps,
    )


def backtest_record(
    result: Backtest, universe: str
) -> tuple[EvidenceRun, Verdict, list[EvalRow], pd.DataFrame]:
    """The run, the verdict, the measured figures and the out-of-sample rows of
    a backtest, as `Store.save_backtest` takes them."""
    ev = result.evaluation
    cost_bps = result.cost * BPS
    run = EvidenceRun(
        universe=universe,
        period=result.period,
        holdout_start=result.holdout_start,
        first_date=ev.first_date,
        last_date=ev.last_date,
        dates=ev.dates,
        months=ev.months,
        rows=ev.rows,
        independent_windows=ev.independent_windows,
        base_rate=ev.base_rate,
        horizon_days=HORIZON,
        cost_bps=cost_bps,
        members=result.members,
        unpriced=len(result.unpriced),
        price_source=result.price_source,
        facts_source=result.facts_source,
        factors_source=result.factors_source,
    )
    decided = result.period == "holdout"
    verdict = Verdict(
        candidate=result.verdict.candidate,
        lightgbm_stays=result.verdict.lightgbm_stays,
        decided=decided,
        checks=[
            CheckShape(name=c.name, value=c.value, passed=c.passed)
            for c in result.verdict.checks
        ],
    )

    rows: list[EvalRow] = []
    for name, model in result.models.items():
        measured = model.evaluation
        selection_ic, selection_spread = result.verdict.basis[name]
        rows += [
            _estimate(name, "mean_ic", measured.mean_ic),
            EvalRow(name, "ic_t_stat", "", _number(measured.ic_t_stat)),
            _estimate(name, "mean_auc", measured.mean_auc),
            _estimate(name, "top_hit_rate", measured.top_hit_rate),
            _estimate(name, "spread_excess", measured.spread),
            EvalRow(name, "selection_ic", "", selection_ic),
            EvalRow(name, "selection_net_spread", "", selection_spread),
            *_portfolio_rows(name, "top", model.top, cost_bps),
            *_portfolio_rows(name, "spread", model.spread, cost_bps),
            *(
                EvalRow(
                    name,
                    "spread_net_at",
                    f"{bps:g}",
                    model.spread.net_at(bps / BPS),
                    net_of_cost_bps=bps,
                )
                for bps in COST_LADDER_BPS
            ),
            EvalRow(name, "dsr_sharpe", "", model.deflated.sharpe),
            EvalRow(name, "dsr_benchmark", "", model.deflated.benchmark),
            EvalRow(name, "dsr_probability", "", model.deflated.probability),
            EvalRow(name, "dsr_trials", "", model.deflated.trials),
        ]
        if model.alpha is not None:
            rows += [
                EvalRow(name, "alpha_annual", "", model.alpha.annual),
                EvalRow(name, "alpha_t_stat", "", model.alpha.t_stat),
                EvalRow(name, "alpha_r2", "", model.alpha.r2),
                EvalRow(name, "alpha_months", "", model.alpha.months),
                *(
                    EvalRow(name, "alpha_loading", factor, loading)
                    for factor, loading in model.alpha.loadings.items()
                ),
            ]
    for bucket in ev.by_score:
        key = str(bucket.score)
        rows += [
            EvalRow("lightgbm", "score_rows", key, bucket.rows),
            _estimate("lightgbm", "score_hit_rate", bucket.hit_rate, key),
            _estimate("lightgbm", "score_mean_excess", bucket.mean_excess, key),
        ]
    for feature in result.features:
        rows += [
            _estimate("", "feature_ic", feature.mean_ic, feature.feature),
            EvalRow("", "feature_t_stat", feature.feature, _number(feature.t_stat)),
            EvalRow("", "feature_dates", feature.feature, feature.dates),
        ]
    return run, verdict, rows, result.predictions


def _portfolio_rows(
    model: str, label: str, strategy: Strategy, cost_bps: float
) -> list[EvalRow]:
    rows = [
        _estimate(model, f"{label}_gross", strategy.gross),
        _estimate(model, f"{label}_net", strategy.net, net_of_cost_bps=cost_bps),
        EvalRow(model, f"{label}_sharpe", "", _number(strategy.sharpe)),
        EvalRow(model, f"{label}_turnover", "", strategy.turnover),
    ]
    if strategy.break_even is not None:
        rows.append(_estimate(model, f"{label}_break_even", strategy.break_even))
    return rows


class _Figures:
    """The stored figures of a backtest, looked up by model, metric and key."""

    def __init__(self, rows: Iterable[EvalRow]) -> None:
        self._rows = {(r.model, r.metric, r.key): r for r in rows}

    def row(self, model: str, metric: str, key: str = "") -> EvalRow:
        return self._rows[model, metric, key]

    def has(self, model: str, metric: str, key: str = "") -> bool:
        return (model, metric, key) in self._rows

    def value(self, model: str, metric: str, key: str = "") -> float | None:
        return self.row(model, metric, key).value

    def number(self, model: str, metric: str, key: str = "") -> float:
        value = self.value(model, metric, key)
        if value is None:
            raise ValueError(f"{model} {metric} {key} is not a number")
        return value

    def interval(self, model: str, metric: str, key: str = "") -> Interval:
        row = self.row(model, metric, key)
        return Interval(value=row.value, lo=row.lo, hi=row.hi)

    def keys(self, model: str, metric: str) -> list[str]:
        """The keys stored for a model's metric, in the order stored."""
        return [key for (m, name, key) in self._rows if (m, name) == (model, metric)]


def build_evidence(store: Store, universe: str) -> EvidenceReport | None:
    """The proof page of `universe`: the held-out backtest if there is one,
    else the development one, and None before any backtest."""
    stored = store.latest_backtest(universe)
    if stored is None:
        return None
    figures = _Figures(stored.rows)
    return EvidenceReport(
        run=stored.run,
        verdict=stored.verdict,
        models=[_model(figures, name, stored) for name in MODELS],
        by_year=_by_year(stored),
        by_score=[
            ScoreEvidence(
                score=int(key),
                rows=int(figures.number("lightgbm", "score_rows", key)),
                hit_rate=figures.interval("lightgbm", "score_hit_rate", key),
                mean_excess=figures.interval("lightgbm", "score_mean_excess", key),
            )
            for key in figures.keys("lightgbm", "score_rows")
        ],
        features=[
            FeatureEvidence(
                feature=key,
                family=family_of(key),
                mean_ic=figures.interval("", "feature_ic", key),
                t_stat=figures.value("", "feature_t_stat", key),
                dates=int(figures.number("", "feature_dates", key)),
            )
            for key in figures.keys("", "feature_ic")
        ],
        feature_hurdle_t=HURDLE_T,
        trials=store.trials(),
    )


def _model(figures: _Figures, name: str, stored: StoredBacktest) -> ModelEvidence:
    def portfolio(label: str) -> Portfolio:
        return Portfolio(
            gross=figures.interval(name, f"{label}_gross"),
            net=figures.interval(name, f"{label}_net"),
            sharpe=figures.value(name, f"{label}_sharpe"),
            turnover=figures.number(name, f"{label}_turnover"),
            break_even=figures.interval(name, f"{label}_break_even")
            if figures.has(name, f"{label}_break_even")
            else None,
        )

    alpha = (
        FactorAlpha(
            annual=figures.number(name, "alpha_annual"),
            t_stat=figures.number(name, "alpha_t_stat"),
            r2=figures.number(name, "alpha_r2"),
            months=int(figures.number(name, "alpha_months")),
            loadings={
                key: figures.number(name, "alpha_loading", key)
                for key in figures.keys(name, "alpha_loading")
            },
        )
        if figures.has(name, "alpha_annual")
        else None
    )
    return ModelEvidence(
        model=name,
        mean_ic=figures.interval(name, "mean_ic"),
        ic_t_stat=figures.value(name, "ic_t_stat"),
        mean_auc=figures.interval(name, "mean_auc"),
        top_hit_rate=figures.interval(name, "top_hit_rate"),
        spread_excess=figures.interval(name, "spread_excess"),
        top=portfolio("top"),
        spread=portfolio("spread"),
        ladder=[
            Ladder(cost_bps=float(key), net=figures.number(name, "spread_net_at", key))
            for key in figures.keys(name, "spread_net_at")
        ],
        alpha=alpha,
        deflated=DeflatedSharpe(
            sharpe=figures.number(name, "dsr_sharpe"),
            benchmark=figures.number(name, "dsr_benchmark"),
            probability=figures.number(name, "dsr_probability"),
            trials=int(figures.number(name, "dsr_trials")),
        ),
        selection_ic=figures.number(name, "selection_ic"),
        selection_net_spread=figures.number(name, "selection_net_spread"),
    )


def _by_year(stored: StoredBacktest) -> list[YearEvidence]:
    """The mean rank IC of each model in each calendar year of the stored rows,
    per date like the overall figure and with month-block intervals."""
    per_date = pd.DataFrame(
        {
            model: {
                date: group[model].rank().corr(group["excess"].rank())
                for date, group in stored.oos.groupby(level="date")
                if len(group) >= MIN_NAMES
            }
            for model in MODELS
        }
    ).sort_index()
    years = []
    for year, block in per_date.groupby(pd.DatetimeIndex(per_date.index).year):
        months = pd.DatetimeIndex(block.index).to_period("M").nunique()
        enough = months >= MIN_YEAR_MONTHS
        estimates = mean_by_month_block(block) if enough else None
        years.append(
            YearEvidence(
                year=int(year),
                dates=int(block.dropna(how="all").shape[0]),
                months=int(months),
                ic={
                    model: Interval(
                        value=_number(
                            estimates[i].value if estimates else block[model].mean()
                        ),
                        lo=_number(estimates[i].low) if estimates else None,
                        hi=_number(estimates[i].high) if estimates else None,
                    )
                    for i, model in enumerate(MODELS)
                },
            )
        )
    return years
