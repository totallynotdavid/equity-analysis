"""Fiscal periods of filed facts: which pieces make a trailing twelve months.

A company reports a flow as the year to date at each quarter end and as the
whole year at the fiscal year end. A trailing twelve months (TTM) adds the last
fiscal year to the current year to date and subtracts the same year to date a
year ago. The features and the filing-linked report both read their TTMs from
`ttm_pieces`, so a figure on the page is the figure the model saw.
"""

from typing import TYPE_CHECKING, NamedTuple


if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    import pandas as pd

YEAR = 365
# Fiscal quarters of 52 and 53 week years differ by a few days. Cumulative
# periods of a fiscal year end after about 3, 6, 9 and 12 months.
SPANS = ((80, 100), (170, 195), (260, 285), (350, 380))
ANNUAL = SPANS[-1]
YEAR_AGO_DAYS = 14


class Fact(NamedTuple):
    """A period as days since 1970. A balance-sheet value has `start == end`.
    `ref` is the caller's handle on the filed row the value came from."""

    start: int
    end: int
    value: float
    ref: int = -1


def span(fact: Fact) -> int:
    return fact.end - fact.start


_CUMULATIVE_SPANS = frozenset(
    days for low, high in SPANS for days in range(low, high + 1)
)


def is_cumulative(fact: Fact) -> bool:
    return span(fact) in _CUMULATIVE_SPANS


def is_annual(fact: Fact) -> bool:
    return ANNUAL[0] <= span(fact) <= ANNUAL[1]


def days_since_epoch(dates: pd.Series) -> list[int]:
    """Convert a date column to integer days since 1970."""
    days: list[int] = (
        dates.astype("datetime64[ns]").astype("int64").floordiv(86_400 * 10**9).tolist()
    )
    return days


def near(facts: Iterable[Fact], target: int, days: int) -> list[Fact]:
    """Facts ending within `days` of `target`, the closest first."""
    low, high = target - days, target + days
    close = [fact for fact in facts if low <= fact.end <= high]
    close.sort(key=lambda fact: abs(fact.end - target))
    return close


def ttm_pieces(facts: Sequence[Fact], end: int) -> list[tuple[int, Fact]] | None:
    """The signed pieces of the twelve months to `end`, the current period
    first: the year to date, plus the last fiscal year, minus the same year to
    date a year ago. A year to date that is a full year is its own TTM. None if
    a piece is missing."""
    cumulative = [fact for fact in facts if fact.end == end and is_cumulative(fact)]
    if not cumulative:
        return None
    current = max(cumulative, key=span)
    if is_annual(current):
        return [(1, current)]
    fiscal_year = [
        fact for fact in near(facts, current.start - 1, 7) if is_annual(fact)
    ]
    year_ago = [
        fact
        for fact in near(facts, end - YEAR, YEAR_AGO_DAYS)
        if abs(span(fact) - span(current)) <= 10
    ]
    if not fiscal_year or not year_ago:
        return None
    return [(1, current), (1, fiscal_year[0]), (-1, year_ago[0])]


def ttm(facts: Sequence[Fact], end: int) -> float | None:
    pieces = ttm_pieces(facts, end)
    if pieces is None:
        return None
    return sum(sign * fact.value for sign, fact in pieces)


def cumulative_ends(facts: Iterable[Fact]) -> list[int]:
    """Every period end that has a cumulative fact, the latest first."""
    return sorted({fact.end for fact in facts if is_cumulative(fact)}, reverse=True)


def latest_average(facts: Sequence[Fact]) -> Fact | None:
    """The cumulative fact with the latest end, the longest period among
    those. A quantity such as the weighted share count has no TTM; this is the
    average over the latest period reported."""
    cumulative = [fact for fact in facts if is_cumulative(fact)]
    if not cumulative:
        return None
    return max(cumulative, key=lambda fact: (fact.end, span(fact)))
