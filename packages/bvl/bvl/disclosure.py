"""Do BVL prices and traded amounts move before a material event is filed?

Day 0 of an event is the first market day on or after the filing date,
whatever the filing minute. A filing made during a session then counts its own
day as day 0, so a same-day reaction never lands in the pre-event window.

Each measure compares an event with placebo days of the same stock: days at
least `QUIET` market days from any filing of its issuer. The excess over the
placebo is the answer, and its interval comes from a bootstrap that resamples
event days, since many issuers file on the same deadline day.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd


if TYPE_CHECKING:
    from collections.abc import Mapping

# Windows in market days relative to day 0, both ends included.
WINDOWS: Mapping[str, tuple[int, int]] = {
    "pre10": (-10, -1),
    "pre5": (-5, -1),
    "pre1": (-1, -1),
    "event": (0, 1),
    "post": (2, 10),
}
# The baseline amount is the mean over these market days before day 0.
BASELINE = (-250, -30)
MIN_BASELINE_TRADES = 10
# A price older than this many market days is unknown.
STALE_AFTER = 20
# An event is clean when its issuer filed nothing in the prior CLEAN_GAP days.
CLEAN_GAP = 10
# Placebo days sit at least this far from any filing of the issuer, and at
# most PLACEBO_RANGE market days from their event, so that they share its
# liquidity regime.
QUIET = 15
PLACEBO_RANGE = 250
PLACEBOS_PER_EVENT = 20


@dataclass(frozen=True)
class Grid:
    """Market days by stocks: log close carried forward, amount, and trade flag."""

    days: pd.DatetimeIndex
    tickers: pd.Index
    log_price: np.ndarray
    amount: np.ndarray
    traded: np.ndarray
    log_market: np.ndarray


def build_grid(quotes: pd.DataFrame, log_market: pd.Series) -> Grid:
    """Lay `quotes` on the market days of `log_market`, a log index level."""
    days = pd.DatetimeIndex(log_market.index)
    close = quotes.pivot_table(index="date", columns="ticker", values="close")
    close = close.reindex(days)
    amount = quotes.pivot_table(index="date", columns="ticker", values="amount")
    amount = amount.reindex(index=days, columns=close.columns)
    log_price = close.apply(np.log).ffill(limit=STALE_AFTER)
    return Grid(
        days=days,
        tickers=close.columns,
        log_price=log_price.to_numpy(),
        amount=amount.fillna(0.0).to_numpy(),
        traded=close.notna().to_numpy(),
        log_market=log_market.to_numpy(dtype=float),
    )


def day_zero(filed: pd.Series, days: pd.DatetimeIndex) -> np.ndarray:
    """Position of the first market day on or after each filing date."""
    dates = pd.to_datetime(filed).dt.normalize()
    return np.asarray(days.searchsorted(dates, side="left"))


def most_traded(grid: Grid, candidates: pd.DataFrame) -> pd.DataFrame:
    """Keep, per event, the candidate ticker that traded most in its baseline.

    `candidates` holds one row per event and ticker of its issuer (`event`,
    `ticker`, `day0`). An issuer renamed its ticker or lists several share
    classes, and the busiest one at the time of each event stands for it.
    """
    lo, hi = BASELINE
    columns = grid.tickers.get_indexer(pd.Index(candidates["ticker"]))
    known = columns >= 0
    candidates, columns = candidates[known], columns[known]
    day0 = candidates["day0"].to_numpy()
    cumulative = np.vstack(
        [np.zeros((1, len(grid.tickers)), dtype=int), grid.traded.cumsum(axis=0)]
    )
    total = len(grid.days)
    end = np.clip(day0 + hi + 1, 0, total)
    start = np.clip(day0 + lo, 0, total)
    trades = cumulative[end, columns] - cumulative[start, columns]
    ranked = candidates.assign(column=columns, trades=trades)
    ranked = ranked.sort_values(["event", "trades"], ascending=[True, False])
    return ranked.drop_duplicates("event").reset_index(drop=True)


def clean(events: pd.DataFrame) -> pd.Series:
    """Events whose issuer filed nothing in the `CLEAN_GAP` market days before."""
    days = events[["ticker", "day0"]].drop_duplicates().sort_values(["ticker", "day0"])
    days["gap"] = days["day0"] - days.groupby("ticker")["day0"].shift()
    days["clean"] = ~(days["gap"] <= CLEAN_GAP)
    flags = events[["ticker", "day0"]].merge(days, on=["ticker", "day0"], how="left")
    return pd.Series(flags["clean"].to_numpy(), index=events.index)


def measure(grid: Grid, columns: np.ndarray, day0: np.ndarray) -> pd.DataFrame:
    """Abnormal return, amount ratio and trade share in each window.

    The abnormal return of a window is the stock's log price change across it
    less the market's. The amount ratio is the mean daily amount in the window
    over the mean in the baseline, with no-trade days as zero. Every measure
    of an event that `_usable` rejects is NaN.
    """
    last = max(b for _, b in WINDOWS.values())
    valid, day0, baseline = _usable(grid, columns, day0, last)
    out: dict[str, np.ndarray] = {}
    for name, (a, b) in WINDOWS.items():
        start, end = day0 + a - 1, day0 + b
        stock = grid.log_price[end, columns] - grid.log_price[start, columns]
        market = grid.log_market[end] - grid.log_market[start]
        rows = day0[:, None] + np.arange(a, b + 1)[None, :]
        amount = grid.amount[rows, columns[:, None]].mean(axis=1)
        out[f"car_{name}"] = np.where(valid, stock - market, np.nan)
        with np.errstate(invalid="ignore", divide="ignore"):
            out[f"volume_{name}"] = np.where(valid, amount / baseline, np.nan)
        out[f"traded_{name}"] = np.where(
            valid, grid.traded[rows, columns[:, None]].mean(axis=1), np.nan
        )
    return pd.DataFrame(out)


def profile(
    grid: Grid, columns: np.ndarray, day0: np.ndarray, span: tuple[int, int]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Daily abnormal return, amount ratio and trade flag for days in `span`.

    Each array has one row per event and one column per day of `span`, both
    ends included. Rows of events too close to either end of the panel are NaN.
    """
    a, b = span
    valid, day0, baseline = _usable(grid, columns, day0, b)
    rows = day0[:, None] + np.arange(a - 1, b + 1)[None, :]
    stock = np.diff(grid.log_price[rows, columns[:, None]], axis=1)
    market = np.diff(grid.log_market[rows], axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        amount = grid.amount[rows[:, 1:], columns[:, None]] / baseline[:, None]
    traded = grid.traded[rows[:, 1:], columns[:, None]].astype(float)
    mask = ~valid[:, None]
    returns = np.where(mask, np.nan, stock - market)
    return returns, np.where(mask, np.nan, amount), np.where(mask, np.nan, traded)


def _usable(
    grid: Grid, columns: np.ndarray, day0: np.ndarray, last: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Which events can be measured, their day 0 and their baseline amount.

    An event needs its whole baseline and `last` days after day 0 inside the
    panel, and `MIN_BASELINE_TRADES` trades in the baseline: a stock that has
    stopped trading has a stale price, not a quiet one. Unusable events get a
    harmless day 0 so that indexing stays in bounds.
    """
    lo, hi = BASELINE
    inside = (day0 + lo >= 0) & (day0 + last < len(grid.days))
    day0 = np.where(inside, day0, -lo)
    rows = day0[:, None] + np.arange(lo, hi + 1)[None, :]
    trades = grid.traded[rows, columns[:, None]].sum(axis=1)
    baseline = grid.amount[rows, columns[:, None]].mean(axis=1)
    return inside & (trades >= MIN_BASELINE_TRADES), day0, baseline


def placebo_days(
    grid: Grid,
    events: pd.DataFrame,
    filing_days: Mapping[str, np.ndarray],
    rng: np.random.Generator,
) -> pd.DataFrame:
    """`PLACEBOS_PER_EVENT` quiet days of the same stock near each event.

    `filing_days` holds, per ticker, the day 0 of every filing of its issuer,
    including filings left out of `events`.
    """
    total = len(grid.days)
    lo, _ = BASELINE
    rows = []
    for ticker, group in events.groupby("ticker"):
        column = int(grid.tickers.get_indexer(pd.Index([ticker]))[0])
        busy = np.zeros(total, dtype=bool)
        for day0 in filing_days[str(ticker)]:
            busy[max(day0 - QUIET, 0) : min(day0 + QUIET + 1, total)] = True
        busy[:-lo] = True
        busy[total - QUIET :] = True
        quiet = np.flatnonzero(~busy)
        for index, day0 in zip(group.index, group["day0"], strict=True):
            near = quiet[
                quiet.searchsorted(day0 - PLACEBO_RANGE) : quiet.searchsorted(
                    day0 + PLACEBO_RANGE, side="right"
                )
            ]
            if near.size == 0:
                continue
            for placebo in rng.choice(near, size=PLACEBOS_PER_EVENT):
                rows.append((index, ticker, column, int(placebo)))
    return pd.DataFrame(rows, columns=["event", "ticker", "column", "day0"])


def excess(
    actual: pd.Series,
    placebo: pd.Series,
    clusters: pd.Series,
    statistic: str,
    rng: np.random.Generator,
    draws: int = 1000,
) -> tuple[float, float, float]:
    """The event statistic less the placebo one, with a 95% cluster bootstrap.

    `actual` and `clusters` share the event index; `placebo` is indexed by the
    event it was drawn for, so a draw that takes an event takes its placebo
    days with it. `statistic` is `mean` or `median`.
    """
    codes, uniques = pd.factorize(clusters)
    cluster_of = pd.Series(codes, index=clusters.index)
    actual = actual.dropna()
    placebo = placebo.dropna()
    placebo = placebo[placebo.index.isin(cluster_of.index)]
    a_values, a_codes = _by_value(actual, cluster_of)
    p_values, p_codes = _by_value(placebo, cluster_of)
    stat = _weighted_median if statistic == "median" else _weighted_mean

    groups = len(uniques)
    ones = np.ones(groups)
    point = stat(a_values, ones[a_codes]) - stat(p_values, ones[p_codes])
    weights = rng.multinomial(groups, np.full(groups, 1 / groups), size=draws)
    estimates = [
        stat(a_values, w[a_codes]) - stat(p_values, w[p_codes]) for w in weights
    ]
    low, high = np.nanpercentile(estimates, [2.5, 97.5])
    return point, float(low), float(high)


def _by_value(
    values: pd.Series, cluster_of: pd.Series
) -> tuple[np.ndarray, np.ndarray]:
    """`values` sorted, with the cluster code of the event behind each."""
    order = np.argsort(values.to_numpy(), kind="stable")
    codes = cluster_of.loc[values.index].to_numpy()
    return values.to_numpy()[order], codes[order]


def _weighted_mean(values: np.ndarray, weights: np.ndarray) -> float:
    total = weights.sum()
    return float(values @ weights / total) if total else np.nan


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    """Median of `values`, already sorted, each repeated `weights` times."""
    cumulative = np.cumsum(weights)
    if cumulative.size == 0 or cumulative[-1] == 0:
        return np.nan
    return float(values[np.searchsorted(cumulative, cumulative[-1] / 2)])
