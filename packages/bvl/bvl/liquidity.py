"""How liquid the BVL has been since 1996, in measures that need no currency.

The API reports a traded amount without its currency, and BVL stocks trade in
soles or US dollars, so totals across stocks are not comparable. Each measure
here compares a stock only with itself or counts days and stocks:

- coverage: the share of market days a stock traded, between its first and
  last trade;
- active stocks: how many local stocks traded in a year;
- illiquidity: the Amihud ratio |return| / amount, averaged per stock and
  month. A currency multiplies a stock's ratio by a constant, so a stock fixed
  effect removes it; the yearly effects that remain trace the market.
"""

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from bvl import panel


if TYPE_CHECKING:
    from pathlib import Path

# A stock month needs this many traded days to give an Amihud ratio.
MIN_DAYS_PER_MONTH = 5


def local_quotes(db: Path) -> pd.DataFrame:
    quotes = panel.load_quotes(db)
    local = [
        panel.is_local_share(ticker, name)
        for ticker, name in zip(quotes["ticker"], quotes["name"], strict=True)
    ]
    return quotes[local].reset_index(drop=True)


def coverage_by_year(quotes: pd.DataFrame) -> pd.DataFrame:
    """Per year: active stocks, and the median and spread of their coverage."""
    days = panel.market_days(quotes)
    spans = quotes.groupby("ticker")["date"].agg(first="min", last="max")
    stock_years = (
        quotes.assign(year=quotes["date"].dt.year)
        .groupby(["ticker", "year"])
        .size()
        .rename("traded")
        .reset_index()
        .join(spans, on="ticker")
    )
    year = stock_years["year"].astype(str)
    lower = np.maximum(stock_years["first"], pd.to_datetime(year + "-01-01"))
    upper = np.minimum(stock_years["last"], pd.to_datetime(year + "-12-31"))
    possible = days.searchsorted(upper, side="right") - days.searchsorted(lower)
    stock_years["coverage"] = stock_years["traded"] / possible
    coverage = stock_years.groupby("year")["coverage"]
    return pd.DataFrame(
        {
            "stocks": coverage.size(),
            "median_coverage": coverage.median(),
            "daily_share": coverage.agg(lambda c: (c >= 0.8).mean()),
            "rare_share": coverage.agg(lambda c: (c < 0.2).mean()),
        }
    )


def monthly_amihud(quotes: pd.DataFrame) -> pd.DataFrame:
    """Mean daily |return| / amount per stock and month, with enough days."""
    daily = quotes.dropna(subset=["log_return"])
    daily = daily[daily["amount"] > 0]
    ratio = daily["log_return"].abs() / daily["amount"]
    month = daily["date"].dt.to_period("M")
    grouped = ratio.groupby([daily["ticker"], month])
    frame = pd.DataFrame({"amihud": grouped.mean(), "days": grouped.size()})
    frame = frame[(frame["days"] >= MIN_DAYS_PER_MONTH) & (frame["amihud"] > 0)]
    frame.index.names = ["ticker", "month"]
    return frame.reset_index()


def time_effects(
    values: pd.Series, units: pd.Series, periods: pd.Series, iterations: int = 200
) -> pd.Series:
    """Period effects of `values` net of a unit effect, by alternating projections.

    Solves the two-way fixed effects model value = unit + period + error on an
    unbalanced panel. The effects are centred on their mean.
    """
    period_effect = pd.Series(0.0, index=pd.unique(periods))
    for _ in range(iterations):
        unit_effect = (values - periods.map(period_effect)).groupby(units).mean()
        updated = (values - units.map(unit_effect)).groupby(periods).mean()
        change = float((updated - period_effect.reindex(updated.index)).abs().max())
        period_effect = updated
        if change < 1e-10:
            break
    return (period_effect - period_effect.mean()).sort_index()


def illiquidity_by_year(amihud: pd.DataFrame) -> pd.Series:
    """Yearly log illiquidity relative to the mean year, net of stock effects."""
    years = amihud["month"].dt.year.rename("year")
    return time_effects(np.log(amihud["amihud"]), amihud["ticker"], years)
