"""Metrics for out-of-sample predictions.

Rows are weekly snapshots, and each label looks 63 trading days ahead, so
neighbouring snapshots share most of their outcome. Intervals resample whole
months to keep that overlap inside each block, and the t-statistic uses
Newey-West errors. `Evaluation.independent_windows` counts the non-overlapping
63-day windows in the span. That is the effective sample, not the row count.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from index_core.labels import HORIZON
from index_core.model import SNAPSHOT_STEP, auc_from_ranks
from index_core.scoring import SCORE_LEVELS, scores_by_date


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


def mean_by_month_block(values: pd.DataFrame) -> list[Estimate]:
    """The mean of each column over its dates, a missing value skipped.

    `values` is indexed by date. The interval resamples whole months, as
    `evaluate` does.
    """
    months = pd.DatetimeIndex(values.index).to_period("M")
    labels = sorted(months.unique())
    data = values.to_numpy(dtype=float)
    sums = np.stack([np.nansum(data[months == month], axis=0) for month in labels])
    counts = np.stack(
        [(~np.isnan(data[months == month])).sum(axis=0) for month in labels]
    )
    draws = np.random.default_rng(BOOTSTRAP_SEED).integers(
        len(labels), size=(BOOTSTRAP_DRAWS, len(labels))
    )
    estimates = []
    for column in range(data.shape[1]):
        with np.errstate(invalid="ignore", divide="ignore"):
            resampled = sums[draws, column].sum(axis=1) / counts[draws, column].sum(
                axis=1
            )
            value = sums[:, column].sum() / counts[:, column].sum()
        # A column with no values at all, such as a fundamental feature in a run
        # without filings, has no interval.
        if np.isnan(resampled).all():
            estimates.append(Estimate(float("nan"), float("nan"), float("nan")))
            continue
        low, high = np.nanpercentile(resampled, [2.5, 97.5])
        estimates.append(Estimate(float(value), float(low), float(high)))
    return estimates


def evaluate(
    predictions: pd.DataFrame, calendar: pd.DatetimeIndex, model: str = "lightgbm"
) -> Evaluation:
    """Metrics for the `model` column of rows with `label` and `excess`, indexed
    by (`date`, `ticker`).

    A date with fewer than `MIN_NAMES` stocks is left out. `calendar` is the
    trading calendar, which turns the span into a count of independent windows.
    """
    sizes = predictions.groupby(level="date").size()
    frame = predictions[
        predictions.index.get_level_values("date").isin(sizes[sizes >= MIN_NAMES].index)
    ]
    frame = frame[["label", "excess"]].assign(prob=frame[model])
    if frame.empty:
        raise ValueError(f"no date has {MIN_NAMES} stocks to rank")

    scored = frame.join(scores_by_date(frame["prob"]))
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


def _per_date(scored: pd.DataFrame) -> pd.DataFrame:
    """Spearman rank correlation of prob and excess return, and AUC, per date."""
    by_date = scored.groupby(level="date")
    prob_rank = by_date["prob"].rank()
    excess_rank = by_date["excess"].rank()

    # Exclude rows missing either input from the rank-correlation sums.
    paired = prob_rank.notna() & excess_rank.notna()
    x = prob_rank.where(paired, 0.0)
    y = excess_rank.where(paired, 0.0)
    sums = (
        pd.DataFrame(
            {
                "n": paired.astype(float),
                "x": x,
                "y": y,
                "xx": x * x,
                "xy": x * y,
                "yy": y * y,
            }
        )
        .groupby(level="date")
        .sum()
    )
    covariance = sums["n"] * sums["xy"] - sums["x"] * sums["y"]
    spread = (sums["n"] * sums["xx"] - sums["x"] ** 2) * (
        sums["n"] * sums["yy"] - sums["y"] ** 2
    )
    with np.errstate(invalid="ignore", divide="ignore"):
        ic = (covariance / np.sqrt(spread)).where(spread > 0)

    label = scored["label"]
    winners = label == 1
    positives = winners.groupby(level="date").sum().astype(float)
    losers = by_date.size() - label.groupby(level="date").sum()
    rank_sum = prob_rank.where(winners, 0.0).groupby(level="date").sum()
    with np.errstate(invalid="ignore", divide="ignore"):
        area = auc_from_ranks(rank_sum, positives, losers.astype(float))
    area = area.where((positives > 0) & (losers > 0))

    table = pd.DataFrame({"ic": ic, "auc": area}).rename_axis(None)
    return table.dropna(subset=["ic"]).sort_index()


def _month_sums(
    scored: pd.DataFrame, per_date: pd.DataFrame, months: pd.PeriodIndex
) -> np.ndarray:
    """One row per month: the quantities the statistics are ratios of."""
    dates = pd.DatetimeIndex(scored.index.get_level_values("date"))
    labels = months.unique().sort_values()
    count = len(labels)
    cell = labels.get_indexer(dates.to_period("M")) * SCORE_LEVELS + (
        scored["score"].to_numpy(dtype=int) - 1
    )
    sums = np.zeros((count, _AUC_N + 1))
    shape = (count, SCORE_LEVELS)
    sums[:, _COUNT:_HITS] = np.bincount(cell, minlength=count * SCORE_LEVELS).reshape(
        shape
    )
    sums[:, _HITS:_EXCESS] = _binned(
        scored["label"], cell, count * SCORE_LEVELS
    ).reshape(shape)
    sums[:, _EXCESS:_IC] = _binned(
        scored["excess"], cell, count * SCORE_LEVELS
    ).reshape(shape)
    month = labels.get_indexer(months)
    sums[:, _IC] = _binned(per_date["ic"], month, count)
    sums[:, _IC_N] = np.bincount(month, minlength=count)
    sums[:, _AUC] = _binned(per_date["auc"], month, count)
    sums[:, _AUC_N] = np.bincount(
        month[per_date["auc"].notna().to_numpy()], minlength=count
    )
    return sums


def _binned(values: pd.Series, bins: np.ndarray, size: int) -> np.ndarray:
    """Sum `values` in each bin, skipping missing values."""
    data = values.to_numpy(dtype=float)
    present = ~np.isnan(data)
    return np.bincount(bins[present], weights=data[present], minlength=size)
