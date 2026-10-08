"""Metrics for out-of-sample predictions.

Rows are weekly snapshots, and each label looks 63 trading days ahead, so
neighbouring snapshots share most of their outcome. Intervals resample whole
months to keep that overlap inside each block, and the t-statistic uses
Newey-West errors. `Evaluation.independent_windows` counts the non-overlapping
63-day windows in the span. That is the effective sample, not the row count.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import numpy as np
import pandas as pd

from index_core.labels import HORIZON
from index_core.model import SNAPSHOT_STEP, auc
from index_core.scoring import SCORE_LEVELS, decile_scores


if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date

    type Statistic = Callable[[np.ndarray], np.ndarray]

# A decile needs at least one name, so a date needs at least this many.
MIN_NAMES = SCORE_LEVELS
# Labels of snapshots this far apart still overlap.
NEWEY_WEST_LAGS = -(-HORIZON // SNAPSHOT_STEP)
BOOTSTRAP_DRAWS = 2000
BOOTSTRAP_SEED = 0
TOP_SCORE_FLOOR = 8

# Columns of the per-month table that the bootstrap sums.
_COUNT = 0
_HITS = _COUNT + SCORE_LEVELS
_EXCESS = _HITS + SCORE_LEVELS
_IC, _IC_N, _AUC, _AUC_N = range(_EXCESS + SCORE_LEVELS, _EXCESS + SCORE_LEVELS + 4)


@dataclass(frozen=True)
class Estimate:
    """A value with its 95% block-bootstrap interval."""

    value: float
    low: float
    high: float


@dataclass(frozen=True)
class ScoreBucket:
    score: int
    rows: int
    hit_rate: Estimate
    mean_excess: Estimate


@dataclass(frozen=True)
class Evaluation:
    first_date: date
    last_date: date
    dates: int
    months: int
    rows: int
    independent_windows: int
    base_rate: float
    mean_ic: Estimate
    ic_t_stat: float
    mean_auc: Estimate
    top_hit_rate: Estimate
    spread: Estimate
    by_score: list[ScoreBucket]


def newey_west_t(series: np.ndarray, lags: int) -> float:
    """t-statistic of the mean, with Bartlett-weighted autocovariances."""
    count = len(series)
    centred = series - series.mean()
    variance = centred @ centred / count
    for lag in range(1, min(lags, count - 1) + 1):
        weight = 1 - lag / (lags + 1)
        variance += 2 * weight * (centred[lag:] @ centred[:-lag]) / count
    return float(series.mean() / np.sqrt(variance / count))


def evaluate(predictions: pd.DataFrame, calendar: pd.DatetimeIndex) -> Evaluation:
    """Metrics for rows with `prob`, `label` and `excess`, indexed by (`date`, `ticker`).

    A date with fewer than `MIN_NAMES` stocks is left out. `calendar` is the
    trading calendar, which turns the span into a count of independent windows.
    """
    sizes = predictions.groupby(level="date").size()
    frame = predictions[
        predictions.index.get_level_values("date").isin(sizes[sizes >= MIN_NAMES].index)
    ]
    if frame.empty:
        raise ValueError(f"no date has {MIN_NAMES} stocks to rank")

    scored = frame.join(_scores(frame))
    per_date = _per_date(scored)
    scored = scored[scored.index.get_level_values("date").isin(per_date.index)]
    months = pd.DatetimeIndex(per_date.index).to_period("M")
    sums = _month_sums(scored, per_date, months)

    draws = np.random.default_rng(BOOTSTRAP_SEED).integers(
        len(sums), size=(BOOTSTRAP_DRAWS, len(sums))
    )
    resampled = sums[draws].sum(axis=1)
    total = sums.sum(axis=0)

    def estimate(statistic: Statistic) -> Estimate:
        low, high = np.nanpercentile(statistic(resampled), [2.5, 97.5])
        return Estimate(float(statistic(total)), float(low), float(high))

    def score_column(offset: int, score: int) -> list[int]:
        return [offset + score - 1]

    top = range(TOP_SCORE_FLOOR, SCORE_LEVELS + 1)
    first, last = per_date.index[0], per_date.index[-1]
    position = pd.Series(np.arange(len(calendar)), index=calendar)
    with np.errstate(invalid="ignore", divide="ignore"):
        by_score = [
            ScoreBucket(
                score=score,
                rows=int(total[_COUNT + score - 1]),
                hit_rate=estimate(
                    _ratio(score_column(_HITS, score), score_column(_COUNT, score))
                ),
                mean_excess=estimate(
                    _ratio(score_column(_EXCESS, score), score_column(_COUNT, score))
                ),
            )
            for score in range(SCORE_LEVELS, 0, -1)
        ]
        top_mean = _ratio(
            score_column(_EXCESS, SCORE_LEVELS), score_column(_COUNT, SCORE_LEVELS)
        )
        bottom_mean = _ratio(score_column(_EXCESS, 1), score_column(_COUNT, 1))
        return Evaluation(
            first_date=first.date(),
            last_date=last.date(),
            dates=len(per_date),
            months=len(sums),
            rows=len(scored),
            independent_windows=int((position[last] - position[first] + 1) // HORIZON),
            base_rate=float(scored["label"].mean()),
            mean_ic=estimate(_ratio([_IC], [_IC_N])),
            ic_t_stat=newey_west_t(per_date["ic"].to_numpy(), NEWEY_WEST_LAGS),
            mean_auc=estimate(_ratio([_AUC], [_AUC_N])),
            top_hit_rate=estimate(
                _ratio(
                    [_HITS + score - 1 for score in top],
                    [_COUNT + score - 1 for score in top],
                )
            ),
            spread=estimate(lambda table: top_mean(table) - bottom_mean(table)),
            by_score=by_score,
        )


def _ratio(numerator: list[int], denominator: list[int]) -> Statistic:
    """Columns of the month table, summed over the numerator and denominator."""
    return lambda sums: (
        sums[..., numerator].sum(axis=-1) / sums[..., denominator].sum(axis=-1)
    )


def _scores(frame: pd.DataFrame) -> pd.Series:
    """The decile score of every row, ranked among the stocks of its own date."""
    parts = []
    for date, group in frame.groupby(level="date"):
        table = decile_scores(group["prob"].droplevel("date"))
        parts.append(
            pd.Series(
                table["score"].to_numpy(),
                index=pd.MultiIndex.from_product([[date], table["ticker"]]),
            )
        )
    return pd.concat(parts).rename("score").rename_axis(["date", "ticker"])


def _per_date(scored: pd.DataFrame) -> pd.DataFrame:
    """Spearman rank correlation of prob and excess return, and AUC, per date."""
    rows = {}
    for date, group in scored.groupby(level="date"):
        rows[date] = {
            "ic": group["prob"].rank().corr(group["excess"].rank()),
            "auc": auc(group["label"], group["prob"]),
        }
    table = pd.DataFrame.from_dict(rows, orient="index", dtype=float)
    return table.dropna(subset=["ic"]).sort_index()


def _month_sums(
    scored: pd.DataFrame, per_date: pd.DataFrame, months: pd.PeriodIndex
) -> np.ndarray:
    """One row per month: the quantities the statistics are ratios of."""
    dates = pd.DatetimeIndex(scored.index.get_level_values("date"))
    scored_months = dates.to_period("M")
    labels = sorted(months.unique())
    sums = np.zeros((len(labels), _AUC_N + 1))
    for row, month in enumerate(labels):
        group = scored[scored_months == month]
        for score, bucket in group.groupby("score"):
            level = cast("int", score) - 1
            sums[row, _COUNT + level] = len(bucket)
            sums[row, _HITS + level] = bucket["label"].sum()
            sums[row, _EXCESS + level] = bucket["excess"].sum()
        in_month = per_date[months == month]
        sums[row, _IC] = in_month["ic"].sum()
        sums[row, _IC_N] = len(in_month)
        sums[row, _AUC] = in_month["auc"].sum()
        sums[row, _AUC_N] = in_month["auc"].count()
    return sums
