"""The plain-text report that `eq backtest` prints."""

from typing import TYPE_CHECKING

from index_core.feature_ic import HURDLE_T
from index_core.labels import HORIZON, LABEL_SPAN
from index_core.model import SNAPSHOT_STEP
from index_core.walkforward import MODELS


if TYPE_CHECKING:
    from index_core.evaluation import Estimate
    from index_core.pipeline import Backtest
    from index_core.portfolio import Strategy

UNPRICED_SHOWN = 10
COST_LADDER_BPS = (0, 5, 10, 20, 50)
NAMES = {"lightgbm": "LightGBM", "ridge": "ridge", "composite": "composite"}


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
        f"{result.facts_source or 'no'} filings, "
        f"{result.factors_source or 'no'} factors",
        f"Experimental. Net of a {result.cost * 1e4:g} bps one-way cost, an assumed "
        "figure: no source here has bid-ask spreads. The break-even cost is the "
        "measured one.",
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
        *_unpriced(result),
        "",
        f"Base rate, share of stocks beating SPY: {ev.base_rate:.1%}",
        "",
        "Models, on the same rows (95% CI by month)",
        *_models(result),
        "",
        f"Portfolios, rebalanced monthly, {result.cost * 1e4:g} bps one way "
        "(returns a year; turnover is the share of a leg traded each month)",
        *_portfolios(result),
        "",
        "Net decile spread a year by one-way cost, bps",
        *_ladder(result),
        "",
        "Spread alpha on the Fama-French five factors, net, a year",
        *_alpha(result),
        "",
        "Multiple testing: the ledger of this database counts "
        f"{result.models['lightgbm'].deflated.trials} specifications",
        *_deflated(result),
        "",
        f"Rank IC of each feature alone, over the same rows (|t| of {HURDLE_T:g} "
        "or more is the bar for a new factor)",
        *_features(result),
        "",
        f"LightGBM by score, {HORIZON}-day excess return",
        "score   rows   hit rate   95% CI           mean excess   95% CI",
        *(
            f"{bucket.score:>5}  {bucket.rows:>5}   {bucket.hit_rate.value:>7.1%}   "
            f"{_interval(bucket.hit_rate, '.1%'):<15}  "
            f"{bucket.mean_excess.value:>+10.1%}   "
            f"{_interval(bucket.mean_excess, '+.1%')}"
            for bucket in ev.by_score
        ),
        "",
        *_verdict(result),
        "",
        "Caveats: a stock counts only on the dates the universe lists it as a "
        "member. One that stopped trading has no label for its last "
        f"{LABEL_SPAN} trading days, so exits by delisting are under-represented; "
        "in the portfolios it keeps its last close. Trades are at the close after "
        "the signal. "
        + (
            "Fundamentals come from filings dated before each snapshot; a company "
            "without filings has none."
            if result.facts_source
            else "No filings are stored, so only technical features carry signal."
        ),
    ]
    return "\n".join(lines) + "\n"


def _models(result: Backtest) -> list[str]:
    rows = [
        f"{'model':<11} {'mean rank IC':>12}   {'95% CI':<17} {'t':>5}  "
        f"{'AUC':>6}  {'spread':>7}"
    ]
    for name in MODELS:
        ev = result.models[name].evaluation
        rows.append(
            f"{NAMES[name]:<11} {ev.mean_ic.value:>+12.3f}   "
            f"{_interval(ev.mean_ic, '+.3f'):<17} {ev.ic_t_stat:>5.2f}  "
            f"{ev.mean_auc.value:>6.3f}  {ev.spread.value:>+7.1%}"
        )
    rows.append(
        "t is the Newey-West t-statistic of the mean IC. AUC is per date. "
        "spread is the mean excess return of score 10 minus score 1."
    )
    return rows


def _portfolios(result: Backtest) -> list[str]:
    rows = [
        "model       portfolio            gross    net      95% CI (net)"
        "          Sharpe  turnover  break-even cost"
    ]
    for name in MODELS:
        model = result.models[name]
        for label, strategy in (
            ("top decile - all", model.top),
            ("top - bottom", model.spread),
        ):
            rows.append(_portfolio_row(NAMES[name], label, strategy))
    return rows


def _portfolio_row(name: str, label: str, strategy: Strategy) -> str:
    return (
        f"{name:<11} {label:<18} {strategy.gross.value:>+7.1%}  "
        f"{strategy.net.value:>+7.1%}  {_interval(strategy.net, '+.1%'):<20} "
        f"{strategy.sharpe:>6.2f}  {strategy.turnover:>7.0%}  "
        f"{_break_even(strategy)}"
    )


def _break_even(strategy: Strategy) -> str:
    if strategy.break_even is None:
        return "none: no gross edge"
    estimate = strategy.break_even
    return (
        f"{estimate.value * 1e4:>5.1f} bps "
        f"[{estimate.low * 1e4:.1f}, {estimate.high * 1e4:.1f}]"
    )


def _ladder(result: Backtest) -> list[str]:
    rows = ["model        " + "".join(f"{bps:>9}" for bps in COST_LADDER_BPS)]
    for name in MODELS:
        spread = result.models[name].spread
        rows.append(
            f"{NAMES[name]:<11}  "
            + "".join(f"{spread.net_at(bps / 1e4):>+9.1%}" for bps in COST_LADDER_BPS)
        )
    return rows


def _alpha(result: Backtest) -> list[str]:
    if all(model.alpha is None for model in result.models.values()):
        return [
            "not computed: no factors are stored. Run `eq factors`."
            if result.factors_source is None
            else "not computed: the factor file covers too few of the holding months."
        ]
    rows = [
        "model        alpha   t      months   R2    Mkt-RF    SMB     HML     RMW     CMA"
    ]
    for name in MODELS:
        alpha = result.models[name].alpha
        if alpha is None:
            rows.append(f"{NAMES[name]:<11}  too few months")
            continue
        rows.append(
            f"{NAMES[name]:<11} {alpha.annual:>+6.1%} {alpha.t_stat:>5.2f}  "
            f"{alpha.months:>6}  {alpha.r2:>5.2f}  "
            + "  ".join(f"{value:>+6.2f}" for value in alpha.loadings.values())
        )
    return rows


def _deflated(result: Backtest) -> list[str]:
    rows = ["model        monthly Sharpe   chance Sharpe   probability above chance"]
    for name in MODELS:
        d = result.models[name].deflated
        rows.append(
            f"{NAMES[name]:<11} {d.sharpe:>14.3f}   {d.benchmark:>13.3f}   "
            f"{d.probability:>24.3f}"
        )
    return rows


def _features(result: Backtest) -> list[str]:
    rows = ["feature            family        mean IC   95% CI              t   dates"]
    for feature in sorted(result.features, key=lambda f: -abs(f.t_stat)):
        rows.append(
            f"{feature.feature:<18} {feature.family:<11} "
            f"{feature.mean_ic.value:>+8.3f}   "
            f"{_interval(feature.mean_ic, '+.3f'):<18} {feature.t_stat:>+5.2f}  "
            f"{feature.dates:>5}"
        )
    return rows


def _verdict(result: Backtest) -> list[str]:
    verdict = result.verdict
    held_out = result.period == "holdout"
    months = "in the development months" if held_out else "here"
    stays = (
        f"LightGBM beats both baselines on rank IC and net spread {months}"
        if verdict.lightgbm_stays
        else "LightGBM does not beat both baselines on rank IC and net spread "
        f"{months}, so the {NAMES[verdict.candidate]} baseline is the candidate"
    )
    rows = [f"Candidate: {NAMES[verdict.candidate]}. {stays}."]
    if held_out:
        rows.append("  Development months, which chose it (mean rank IC, net spread):")
        rows.extend(
            f"    {NAMES[name]:<11} {ic:>+7.3f}  {spread:>+7.1%}"
            for name, (ic, spread) in verdict.basis.items()
        )
        rows.append("  Held-out months, which test it:")
    rows.extend(
        f"  [{'x' if check.passed else ' '}] {check.name}: {check.value}"
        for check in verdict.checks
    )
    if not held_out:
        rows.append(
            "Verdict: none. This is the development period; run with --final "
            "once for the held-out months."
        )
    elif verdict.show_score:
        rows.append("Verdict: show the score.")
    elif verdict.checks_passed:
        rows.append(
            f"Verdict: do not show the score. The {NAMES[verdict.candidate]} "
            "baseline passes the checks, but the score is LightGBM's."
        )
    else:
        rows.append("Verdict: do not show the score.")
    return rows


def _unpriced(result: Backtest) -> list[str]:
    if not result.unpriced:
        return []
    shown = ", ".join(result.unpriced[:UNPRICED_SHOWN])
    more = len(result.unpriced) - UNPRICED_SHOWN
    more_text = f" and {more} more" if more > 0 else ""
    return [
        f"Missing: no prices for {len(result.unpriced)} of {result.members} "
        f"universe names ({shown}{more_text}). "
        "They are not in the rows."
    ]


def _interval(estimate: Estimate, spec: str) -> str:
    return f"[{estimate.low:{spec}}, {estimate.high:{spec}}]"
