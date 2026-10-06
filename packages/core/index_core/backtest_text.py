"""The plain-text table that `eq backtest` prints."""

from typing import TYPE_CHECKING

from index_core.labels import HORIZON
from index_core.model import SNAPSHOT_STEP


if TYPE_CHECKING:
    from index_core.evaluation import Estimate
    from index_core.pipeline import Backtest


def render(result: Backtest, universe: str) -> str:
    ev = result.evaluation
    first_fold = result.folds[0]
    period = (
        f"holdout, the last months after {result.holdout_start}"
        if result.period == "holdout"
        else f"development, up to {result.holdout_start}"
    )
    lines = [
        f"Walk-forward backtest, {universe} universe, {result.price_source} prices, "
        f"{result.facts_source or 'no'} filings",
        "Experimental, not validated. Gross of costs.",
        "",
        f"Period: {period}",
        f"Out of sample: {ev.first_date} to {ev.last_date}, refit every quarter "
        f"({len(result.folds)} fits, first trained on {first_fold.train_dates} "
        f"snapshots up to {first_fold.last_train.date()})",
        f"Rows: {ev.rows} stock-days on {ev.dates} snapshot dates "
        f"(every {SNAPSHOT_STEP}th trading day) in {ev.months} months",
        f"Effective sample: about {ev.independent_windows} independent "
        f"{HORIZON}-day windows. Snapshots a week apart share most of their "
        "outcome, so the row count overstates the evidence.",
        "",
        f"Base rate, share of stocks beating SPY: {ev.base_rate:.1%}",
        "",
        "metric                                    value   95% CI (by month)",
        _row("mean rank IC", ev.mean_ic, ".3f"),
        _row("mean AUC per date", ev.mean_auc, ".3f"),
        _row("hit rate, score >= 8 (base rate above)", ev.top_hit_rate, ".1%"),
        _row("excess return, score 10 minus score 1", ev.spread, "+.1%"),
        f"Newey-West t-statistic of the mean IC: {ev.ic_t_stat:.2f}",
        "",
        "score   rows   hit rate   95% CI           mean excess   95% CI",
        *(
            f"{bucket.score:>5}  {bucket.rows:>5}   {bucket.hit_rate.value:>7.1%}   "
            f"{_interval(bucket.hit_rate, '.1%'):<15}  "
            f"{bucket.mean_excess.value:>+10.1%}   "
            f"{_interval(bucket.mean_excess, '+.1%')}"
            for bucket in ev.by_score
        ),
        "",
        "Caveats: the universe is a fixed list, not point-in-time membership, so "
        "survivors are over-represented. "
        + (
            "Fundamentals come from filings dated before each snapshot; a company "
            "without filings has none."
            if result.facts_source
            else "No filings are stored, so only technical features carry signal."
        ),
    ]
    return "\n".join(lines) + "\n"


def _interval(estimate: Estimate, spec: str) -> str:
    return f"[{estimate.low:{spec}}, {estimate.high:{spec}}]"


def _row(name: str, estimate: Estimate, spec: str) -> str:
    value = format(estimate.value, spec)
    return f"{name:<40} {value:>8}   {_interval(estimate, spec)}"
