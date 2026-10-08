"""Fundamental features from SEC filings, as known on each date.

A fact is usable from the first trading day after it was filed. Where several
filings report one period, the latest one filed before the date wins. Nothing
is back-filled, and a value is carried forward only until the next filing
changes the information.

`fundamental_inputs` turns filed facts into trailing-twelve-month flows,
balance-sheet values, and share counts known on each date. `fundamental_features`
joins them with the day's price into ratios that compare across companies.
"""

from typing import TYPE_CHECKING, NamedTuple

import numpy as np
import pandas as pd


if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

FEATURES = (
    "sales_to_ev",
    "ebitda_to_ev",
    "earnings_yield",
    "fcf_yield",
    "roa",
    "gross_margin",
    "op_margin",
    "net_margin",
    "revenue_growth",
    "earnings_growth",
    "debt_to_equity",
    "accruals",
    "dilution",
    "asset_growth",
)

INPUTS = (
    "revenue",
    "revenue_prev",
    "gross_profit",
    "operating_income",
    "net_income",
    "net_income_prev",
    "depreciation",
    "operating_cash_flow",
    "capex",
    "total_assets",
    "total_assets_prev",
    "debt",
    "equity",
    "cash",
    "shares",
    "shares_prev",
    "shares_prev_end",
    "as_of",
)
_DATE_INPUTS = ("shares_prev_end", "as_of")

# A value is current for this many days after the period it reads ends, then it
# is missing. A concept filed only once a year is read until the next annual
# filing, which is due 90 days after the next year end. A company that stops
# filing, or drops a concept, therefore stops being scored on old values.
MAX_LAG = 460
# The oldest period a feature reads is a year before a TTM that itself reads two
# years, from a concept that may lag the newest period by about `MAX_LAG`.
_WINDOW = 4 * 365
_YEAR = 365
# Fiscal quarters of 52 and 53 week years differ by a few days. Cumulative
# periods of a fiscal year end after about 3, 6, 9 and 12 months.
_SPANS = ((80, 100), (170, 195), (260, 285), (350, 380))
_ANNUAL = _SPANS[-1]
_YEAR_AGO_DAYS = 14
_BALANCE_DAYS = 20
# A cover-page date moves by several weeks from one year to the next.
_SHARES_DAYS = 60


class _Fact(NamedTuple):
    """A period as days since 1970. A balance-sheet value has `start == end`."""

    start: int
    end: int
    value: float


def fundamental_inputs(facts: pd.DataFrame, calendar: pd.DatetimeIndex) -> pd.DataFrame:
    """The `INPUTS` known at every date of `calendar`, per ticker.

    `facts` is the long frame from `Store.read_facts`: a `ticker` column and
    the `FACT_COLUMNS`. The result is indexed by (`date`, `ticker`). A date
    before a ticker's first usable filing has missing values, and so has a date
    more than `MAX_LAG` days after the period an input reads. A ticker without
    facts has no rows.
    """
    frames = {
        str(ticker): _ticker_inputs(group, calendar)
        for ticker, group in facts.groupby("ticker")
    }
    if not frames:
        empty = pd.MultiIndex.from_arrays([[], []], names=["date", "ticker"])
        return pd.DataFrame(index=empty, columns=list(INPUTS), dtype=float)
    inputs = pd.concat(frames, names=["ticker"]).swaplevel().sort_index()
    return inputs.rename_axis(["date", "ticker"])


def _days(dates: pd.Series) -> list[int]:
    """Days since 1970."""
    days: list[int] = (
        dates.astype("datetime64[ns]").astype("int64").floordiv(86_400 * 10**9).tolist()
    )
    return days


def _ticker_inputs(facts: pd.DataFrame, calendar: pd.DatetimeIndex) -> pd.DataFrame:
    # Among filings of one period on one day, the highest-ranked tag comes last.
    ordered = facts.sort_values(["filed", "priority"], ascending=[True, False])
    first_usable = calendar.searchsorted(ordered["filed"], side="right").tolist()
    starts = _days(ordered["start"].fillna(ordered["end"]))
    ends = _days(ordered["end"])
    concepts = ordered["concept"].tolist()
    values = ordered["value"].tolist()

    # The latest known value of each (concept, period). Later filings overwrite.
    latest: dict[str, dict[tuple[int, int], float]] = {}
    snapshots: dict[pd.Timestamp, dict[str, float]] = {}
    expiries: dict[pd.Timestamp, dict[str, float]] = {}
    newest_end = 0
    for index, position in enumerate(first_usable):
        latest.setdefault(concepts[index], {})[starts[index], ends[index]] = values[
            index
        ]
        newest_end = max(newest_end, ends[index])
        last_of_day = (
            index + 1 == len(first_usable) or first_usable[index + 1] != position
        )
        if last_of_day and position < len(calendar):
            as_of = calendar[position]
            snapshots[as_of], expiries[as_of] = _snapshot(
                latest, newest_end, as_of.value // (86_400 * 10**9)
            )
    columns = list(INPUTS)
    # The row of the last filing day holds from one filing to the next, missing
    # values included, so a dropped concept does not fall back to an older one.
    frame = pd.DataFrame.from_dict(snapshots, orient="index", columns=columns)
    frame = frame.reindex(calendar, method="ffill")
    expiry = pd.DataFrame.from_dict(expiries, orient="index", columns=columns)
    expiry = expiry.reindex(calendar, method="ffill")
    today = pd.Series(_days(pd.Series(calendar)), index=calendar)
    frame = frame.where(expiry.ge(today, axis=0))
    for column in _DATE_INPUTS:
        frame[column] = pd.to_datetime(frame[column], unit="D")
    return frame


def _snapshot(
    latest: dict[str, dict[tuple[int, int], float]], newest_end: int, as_of: int
) -> tuple[dict[str, float], dict[str, float]]:
    """The inputs from the latest known version of every fact, and the last day
    each one is current."""
    rows = {
        concept: [
            _Fact(start, end, value)
            for (start, end), value in versions.items()
            if end >= newest_end - _WINDOW
        ]
        for concept, versions in latest.items()
    }

    def flow(concept: str) -> tuple[float, float, float]:
        return _flow_ttm(rows.get(concept, []))

    def balance(
        concept: str, days: int = _BALANCE_DAYS
    ) -> tuple[float, float, float, float]:
        return _balance(rows.get(concept, []), days)

    revenue, revenue_prev, revenue_end = flow("revenue")
    net_income, net_income_prev, net_income_end = flow("net_income")
    assets, assets_prev, _, assets_end = balance("total_assets")
    shares, shares_prev, shares_prev_end, shares_end = balance("shares", _SHARES_DAYS)
    debt, debt_end = _debt(rows)
    gross_profit, _, gross_profit_end = flow("gross_profit")
    operating_income, _, operating_income_end = flow("operating_income")
    depreciation, _, depreciation_end = flow("depreciation")
    cash_flow, _, cash_flow_end = flow("operating_cash_flow")
    capex, _, capex_end = flow("capex")
    equity, _, _, equity_end = balance("equity")
    cash, _, _, cash_end = balance("cash")
    values = {
        "revenue": (revenue, revenue_end),
        "revenue_prev": (revenue_prev, revenue_end),
        "gross_profit": (gross_profit, gross_profit_end),
        "operating_income": (operating_income, operating_income_end),
        "net_income": (net_income, net_income_end),
        "net_income_prev": (net_income_prev, net_income_end),
        "depreciation": (depreciation, depreciation_end),
        "operating_cash_flow": (cash_flow, cash_flow_end),
        "capex": (capex, capex_end),
        "total_assets": (assets, assets_end),
        "total_assets_prev": (assets_prev, assets_end),
        "debt": (debt, debt_end),
        "equity": (equity, equity_end),
        "cash": (cash, cash_end),
        "shares": (shares, shares_end),
        "shares_prev": (shares_prev, shares_end),
        "shares_prev_end": (shares_prev_end, shares_end),
        "as_of": (float(as_of), shares_end),
    }
    return (
        {name: value for name, (value, _) in values.items()},
        {name: end + MAX_LAG for name, (_, end) in values.items()},
    )


def _span(fact: _Fact) -> int:
    return fact.end - fact.start


def _is_cumulative(fact: _Fact) -> bool:
    return any(low <= _span(fact) <= high for low, high in _SPANS)


def _is_annual(fact: _Fact) -> bool:
    return _ANNUAL[0] <= _span(fact) <= _ANNUAL[1]


def _near(facts: Iterable[_Fact], target: int, days: int) -> list[_Fact]:
    """Facts ending within `days` of `target`, the closest first."""
    close = [fact for fact in facts if abs(fact.end - target) <= days]
    return sorted(close, key=lambda fact: abs(fact.end - target))


def _ttm(facts: Sequence[_Fact], end: int) -> float | None:
    """Twelve months to `end`: the last fiscal year plus this year's cumulative
    period minus the same period a year ago. None if a piece is missing."""
    cumulative = [fact for fact in facts if fact.end == end and _is_cumulative(fact)]
    if not cumulative:
        return None
    current = max(cumulative, key=_span)
    if _is_annual(current):
        return current.value
    fiscal_year = [
        fact for fact in _near(facts, current.start - 1, 7) if _is_annual(fact)
    ]
    year_ago = [
        fact
        for fact in _near(facts, end - _YEAR, _YEAR_AGO_DAYS)
        if abs(_span(fact) - _span(current)) <= 10
    ]
    if not fiscal_year or not year_ago:
        return None
    return fiscal_year[0].value + current.value - year_ago[0].value


def _flow_ttm(facts: list[_Fact]) -> tuple[float, float, float]:
    """The latest TTM, the TTM a year before it and the end of the latest, NaN
    where unknown."""
    ends = sorted({fact.end for fact in facts if _is_cumulative(fact)}, reverse=True)
    for end in ends:
        latest = _ttm(facts, end)
        if latest is None:
            continue
        for fact in _near(facts, end - _YEAR, _YEAR_AGO_DAYS):
            earlier = _ttm(facts, fact.end)
            if earlier is not None:
                return latest, earlier, float(end)
        return latest, np.nan, float(end)
    return np.nan, np.nan, np.nan


def _balance(facts: list[_Fact], days: int) -> tuple[float, float, float, float]:
    """The latest value, the one a year before it, that one's date and the
    latest date."""
    if not facts:
        return np.nan, np.nan, np.nan, np.nan
    latest = max(facts, key=lambda fact: fact.end)
    earlier = _near(facts, latest.end - _YEAR, days)
    if not earlier:
        return latest.value, np.nan, np.nan, float(latest.end)
    return latest.value, earlier[0].value, float(earlier[0].end), float(latest.end)


def _debt(rows: dict[str, list[_Fact]]) -> tuple[float, float]:
    """Long-term debt plus the current portion reported for the same date, and
    that date. A company that reports no current debt then is taken to have
    none."""
    long_term = rows.get("long_term_debt", [])
    if not long_term:
        return np.nan, np.nan
    latest = max(long_term, key=lambda fact: fact.end)
    current = [
        fact.value for fact in rows.get("current_debt", []) if fact.end == latest.end
    ]
    return latest.value + sum(current), float(latest.end)


def fundamental_features(
    inputs: pd.DataFrame, prices: pd.DataFrame, benchmark: str
) -> pd.DataFrame:
    """The `FEATURES` per (`date`, `ticker`) of `inputs`.

    `inputs` comes from `fundamental_inputs` and `prices` is the long frame
    from `Store.read_prices`. Valuation uses the as-traded close and a share
    count scaled for any split since the count was filed. Yields stand in for
    price multiples: they stay defined and keep their order when earnings or
    EBITDA are not positive. A ratio with a non-positive denominator is NaN.
    """
    closes = prices.pivot(index="date", columns="ticker", values="close")
    volumes = prices.pivot(index="date", columns="ticker", values="volume")
    adjusted = prices.pivot(index="date", columns="ticker", values="adj_volume")
    # Adjusted volume over traded volume is the number of shares today that one
    # share on that day became through later splits.
    split = (adjusted / volumes.replace(0, np.nan)).ffill()

    index = inputs.index
    dates = pd.DatetimeIndex(index.get_level_values("date"))
    tickers = index.get_level_values("ticker")
    close = _lookup(closes, dates, tickers)
    split_now = _lookup(split, dates, tickers)
    split_filed = _lookup(split, pd.DatetimeIndex(inputs["as_of"]), tickers)
    split_prev = _lookup(split, pd.DatetimeIndex(inputs["shares_prev_end"]), tickers)

    shares = inputs["shares"].to_numpy() * split_filed / split_now
    market_cap = pd.Series(close * shares, index=index)
    enterprise = market_cap + inputs["debt"] - inputs["cash"]
    positive_ev = enterprise.where(enterprise > 0)
    ebitda = inputs["operating_income"] + inputs["depreciation"]
    free_cash_flow = inputs["operating_cash_flow"] - inputs["capex"]
    revenue = inputs["revenue"].where(inputs["revenue"] > 0)
    assets = inputs["total_assets"].where(inputs["total_assets"] > 0)
    earlier_shares = inputs["shares_prev"].to_numpy() * split_prev / split_filed

    features = pd.DataFrame(
        {
            "sales_to_ev": inputs["revenue"] / positive_ev,
            "ebitda_to_ev": ebitda / positive_ev,
            "earnings_yield": inputs["net_income"] / market_cap,
            "fcf_yield": free_cash_flow / market_cap,
            "roa": inputs["net_income"] / assets,
            "gross_margin": inputs["gross_profit"] / revenue,
            "op_margin": inputs["operating_income"] / revenue,
            "net_margin": inputs["net_income"] / revenue,
            "revenue_growth": inputs["revenue"]
            / inputs["revenue_prev"].where(inputs["revenue_prev"] > 0)
            - 1,
            "earnings_growth": (inputs["net_income"] - inputs["net_income_prev"])
            / inputs["net_income_prev"].abs().replace(0, np.nan),
            "debt_to_equity": inputs["debt"]
            / inputs["equity"].where(inputs["equity"] > 0),
            "accruals": (inputs["net_income"] - inputs["operating_cash_flow"]) / assets,
            "dilution": inputs["shares"] / earlier_shares - 1,
            "asset_growth": inputs["total_assets"]
            / inputs["total_assets_prev"].where(inputs["total_assets_prev"] > 0)
            - 1,
        }
    )[list(FEATURES)]
    return features.replace([np.inf, -np.inf], np.nan)


def _lookup(
    wide: pd.DataFrame, dates: pd.DatetimeIndex, tickers: pd.Index
) -> np.ndarray:
    """`wide` at each (date, ticker) pair, using the latest day on or before the
    date. A date with no earlier day, or a ticker not in `wide`, gives NaN."""
    out = np.full(len(dates), np.nan)
    for ticker in pd.unique(tickers):
        if ticker not in wide.columns:
            continue
        rows = np.flatnonzero(np.asarray(tickers == ticker))
        series = wide[ticker].dropna()
        positions = series.index.searchsorted(dates[rows], side="right") - 1
        valid = positions >= 0
        out[rows[valid]] = series.to_numpy()[positions[valid]]
    return out


def concept_coverage(
    facts: pd.DataFrame, tickers: Sequence[str], as_of: pd.Timestamp
) -> pd.Series:
    """How many concepts each ticker has fresh facts for at `as_of`.

    A fact counts if it was filed before `as_of` and its period ended within
    `MAX_LAG` days of `as_of`. A ticker with no facts has 0.
    """
    known = facts[facts["filed"] < as_of]
    counts = dict.fromkeys(tickers, 0)
    for ticker, group in known.groupby("ticker"):
        fresh = group[group["end"] >= as_of - pd.Timedelta(days=MAX_LAG)]
        counts[str(ticker)] = fresh["concept"].nunique()
    return pd.Series(counts, name="concepts").loc[list(tickers)].astype(int)
