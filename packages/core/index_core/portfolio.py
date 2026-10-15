"""Monthly portfolios built from a score, and what costs do to them.

On the first snapshot of each month the stocks are ranked by score and the top
decile and the bottom decile are held, equal-weighted, until the first snapshot
of the next month. Trades are made at the close after the signal, as the labels
assume. The reference is every scored stock, equal-weighted.

A leg's `traded` is the sum of the absolute weight changes of a rebalance,
buys plus sells, as a fraction of the leg's capital. The first rebalance builds
the leg from cash, so it trades 1. Costs are `cost * traded`, with `cost` the
fraction of the notional lost on each trade, one way. Only the strategy pays:
the equal-weighted reference, which also has to rebalance, is free.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from index_core.evaluation import (
    BOOTSTRAP_DRAWS,
    BOOTSTRAP_SEED,
    MIN_NAMES,
    Estimate,
)
from index_core.labels import ENTRY_LAG
from index_core.scoring import SCORE_LEVELS, decile_scores


MONTHS_PER_YEAR = 12
# Predictions look 63 trading days ahead, about three months, so neighbouring
# months of a strategy are not independent. The bootstrap keeps this many
# in a row.
BLOCK_MONTHS = 3
MIN_PERIODS = 6


@dataclass(frozen=True)
class Holdings:
    """What each monthly rebalance earned before costs, and what it traded.

    Arrays have one entry per holding period. Period `k` runs from the close of
    `entry[k]` to the close of `exit[k]`.
    """

    entry: pd.DatetimeIndex
    exit: pd.DatetimeIndex
    days: np.ndarray
    top: np.ndarray
    bottom: np.ndarray
    universe: np.ndarray
    top_traded: np.ndarray
    bottom_traded: np.ndarray

    @property
    def periods(self) -> int:
        return len(self.entry)


@dataclass(frozen=True)
class Strategy:
    """A monthly return series before costs, its turnover and its net result.

    Returns and `traded` are per month. The estimates are annualised.
    """

    gross_returns: np.ndarray
    traded: np.ndarray
    cost: float
    gross: Estimate
    net: Estimate
    sharpe: float
    monthly_sharpe: float
    turnover: float
    break_even: Estimate | None

    @property
    def net_returns(self) -> np.ndarray:
        return self.gross_returns - self.cost * self.traded

    def net_at(self, cost: float) -> float:
        """The annualised mean net return if each trade lost `cost`."""
        return float(MONTHS_PER_YEAR * (self.gross_returns - cost * self.traded).mean())


def monthly_holdings(scores: pd.Series, adj_close: pd.DataFrame) -> Holdings:
    """Hold the top and bottom decile of `scores` for each month.

    `scores` is indexed by (`date`, `ticker`) and `adj_close` is the adjusted
    close by trading day and ticker. A stock whose prices end keeps its last
    close, so a delisting does not drop it from the leg. A stock without a close
    on its trade day is not bought. A month with fewer than `MIN_NAMES` stocks
    to buy is skipped, and the next month starts from cash.
    """
    calendar = adj_close.index
    closes = adj_close.ffill()
    dates = pd.DatetimeIndex(
        scores.index.get_level_values("date").unique()
    ).sort_values()
    signals = dates.to_series().groupby(dates.to_period("M")).first()
    entries = calendar.get_indexer(pd.DatetimeIndex(signals)) + ENTRY_LAG

    entry: list[pd.Timestamp] = []
    exit_: list[pd.Timestamp] = []
    days: list[int] = []
    columns: dict[str, list[float]] = {
        name: []
        for name in ("top", "bottom", "universe", "top_traded", "bottom_traded")
    }
    previous: _Held | None = None
    for signal, start, end in zip(
        signals.iloc[:-1], entries[:-1], entries[1:], strict=True
    ):
        if end >= len(calendar):
            break
        day_scores = scores.xs(signal, level="date")
        entry_close = closes.iloc[start].reindex(day_scores.index)
        exit_close = closes.iloc[end].reindex(day_scores.index)
        returns = (exit_close / entry_close - 1).dropna()
        if len(returns) < MIN_NAMES:
            previous = None
            continue
        table = decile_scores(day_scores.reindex(returns.index))
        held = _Held(
            _equal_weights(table, SCORE_LEVELS), _equal_weights(table, 1), returns
        )
        top_traded, bottom_traded = held.traded_from(previous)
        entry.append(calendar[start])
        exit_.append(calendar[end])
        days.append(int(end - start))
        columns["top"].append(float(returns[held.top.index].mean()))
        columns["bottom"].append(float(returns[held.bottom.index].mean()))
        columns["universe"].append(float(returns.mean()))
        columns["top_traded"].append(top_traded)
        columns["bottom_traded"].append(bottom_traded)
        previous = held

    if len(entry) < MIN_PERIODS:
        raise ValueError(
            f"{len(entry)} monthly rebalances are too few to judge a portfolio; "
            f"need {MIN_PERIODS}"
        )
    return Holdings(
        entry=pd.DatetimeIndex(entry),
        exit=pd.DatetimeIndex(exit_),
        days=np.array(days),
        **{name: np.array(values) for name, values in columns.items()},
    )


def _equal_weights(table: pd.DataFrame, score: int) -> pd.Series:
    names = table.loc[table["score"] == score, "ticker"]
    return pd.Series(1 / len(names), index=pd.Index(names))


@dataclass(frozen=True)
class _Held:
    """The legs bought at a rebalance and what every scored stock returned."""

    top: pd.Series
    bottom: pd.Series
    returns: pd.Series

    def traded_from(self, previous: _Held | None) -> tuple[float, float]:
        """Buys plus sells of the top and bottom leg, after `previous` drifted."""
        if previous is None:
            return 1.0, 1.0
        return (
            _traded(self.top, previous.drifted(previous.top)),
            _traded(self.bottom, previous.drifted(previous.bottom)),
        )

    def drifted(self, weights: pd.Series) -> pd.Series:
        """Weights after the period's returns, before the next rebalance."""
        grown = weights * (1 + self.returns[weights.index])
        return grown / grown.sum()


def _traded(new: pd.Series, drifted: pd.Series) -> float:
    return float(new.sub(drifted, fill_value=0.0).abs().sum())


def strategy(gross_returns: np.ndarray, traded: np.ndarray, cost: float) -> Strategy:
    """Net result of a monthly return series that traded `traded` each month.

    The intervals resample blocks of `BLOCK_MONTHS` months. The break-even cost
    is the one that makes the mean net return zero, `mean gross / mean traded`.
    It does not exist when the gross mean is not positive.
    """
    draws = _block_draws(len(gross_returns))
    resampled_gross = gross_returns[draws].mean(axis=1)
    resampled_traded = traded[draws].mean(axis=1)
    annual = MONTHS_PER_YEAR

    def interval(value: float, resampled: np.ndarray) -> Estimate:
        low, high = np.percentile(resampled, [2.5, 97.5])
        return Estimate(float(value), float(low), float(high))

    net_returns = gross_returns - cost * traded
    gross_mean = float(gross_returns.mean())
    break_even = (
        interval(
            gross_mean / traded.mean(),
            resampled_gross / resampled_traded,
        )
        if gross_mean > 0
        else None
    )
    deviation = net_returns.std(ddof=1)
    monthly_sharpe = float(net_returns.mean() / deviation) if deviation > 0 else np.nan
    return Strategy(
        gross_returns=gross_returns,
        traded=traded,
        cost=cost,
        gross=interval(annual * gross_mean, annual * resampled_gross),
        net=interval(
            annual * net_returns.mean(),
            annual * (resampled_gross - cost * resampled_traded),
        ),
        sharpe=monthly_sharpe * float(np.sqrt(annual)),
        monthly_sharpe=monthly_sharpe,
        turnover=float(traded.mean()),
        break_even=break_even,
    )


def _block_draws(periods: int) -> np.ndarray:
    """Indices of `BOOTSTRAP_DRAWS` series made of wrapped blocks of months."""
    blocks = -(-periods // BLOCK_MONTHS)
    starts = np.random.default_rng(BOOTSTRAP_SEED).integers(
        periods, size=(BOOTSTRAP_DRAWS, blocks)
    )
    wrapped = (starts[:, :, None] + np.arange(BLOCK_MONTHS)) % periods
    return wrapped.reshape(BOOTSTRAP_DRAWS, -1)[:, :periods]
