"""The fundamentals of one company with the filed figure behind every value.

`statements` evaluates `metrics.METRICS` on the facts known on a date. A trailing
twelve months comes from the same `periods.ttm_pieces` the model's features
use. Each point lists the figures it is made of, and each figure names the
filing it first appeared in.
"""

from datetime import date
from functools import cache
from typing import TYPE_CHECKING, Any, NamedTuple

import numpy as np
import pandas as pd

from index_core.metrics import METRICS, evaluate
from index_core.periods import (
    Fact,
    days_since_epoch,
    is_cumulative,
    span,
    ttm_pieces,
)
from index_core.report import (
    Filing,
    Part,
    Point,
    Risk,
    Series,
    TickerReport,
    Valuation,
)
from index_core.sources.edgar import CONCEPTS, filing_url


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

# Keep three years of quarterly points for each metric.
POINTS = 12

_UNIX_EPOCH = date(1970, 1, 1).toordinal()

# Signed facts that make up a quantity at one period end.
type _Pieces = list[tuple[int, Fact]]


class _Quantity(NamedTuple):
    """Readable period ends and the pieces available at each end."""

    name: str
    ends: set[int]
    at: Callable[[int], _Pieces | None]


def statements(
    facts: pd.DataFrame, as_of: date | pd.Timestamp, points: int = POINTS
) -> dict[str, list[Point]]:
    """The latest `points` points of every metric that can be computed, oldest
    first, from the facts filed before `as_of`.

    `facts` is one ticker's frame from `Store.read_facts`. Where several
    filings report a period, the latest one filed counts, and a figure links to
    the first filing that reported that value.
    """
    book = _Book(facts, pd.Timestamp(as_of))
    quantities: dict[str, _Quantity] = {}

    def quantity(name: str) -> _Quantity:
        if name not in quantities:
            quantities[name] = book.quantity(name)
        return quantities[name]

    result: dict[str, list[Point]] = {}
    for name, metric in METRICS.items():
        inputs = [quantity(i) for i in metric.inputs]
        found: list[Point] = []
        for end in sorted(set.intersection(*(i.ends for i in inputs)), reverse=True):
            pieces = [i.at(end) for i in inputs]
            if any(piece is None for piece in pieces):
                continue
            # Filings print at most four decimals; rounding drops the float
            # noise of adding and subtracting them.
            values = [
                round(sum(sign * fact.value for sign, fact in piece), 6)
                for piece in pieces
                if piece is not None
            ]
            value = float(evaluate(metric, values))
            if np.isfinite(value):
                found.append(
                    Point(
                        end=_day(end),
                        value=value,
                        formula=metric.formula,
                        parts=[
                            book.part(i.name, sign, fact)
                            for i, piece in zip(inputs, pieces, strict=True)
                            for sign, fact in piece or []
                        ],
                    )
                )
                if len(found) == points:
                    break
        if found:
            result[name] = found[::-1]
    return result


def ticker_report(
    ticker: str,
    universe: str,
    as_of: date | pd.Timestamp,
    facts_source: str,
    security: Mapping[str, Any],
    facts: pd.DataFrame,
    percentiles: Mapping[str, tuple[float | None, float | None]],
    valuation: Valuation | None = None,
    risk: Risk | None = None,
) -> TickerReport | None:
    """The report of one ticker, or None where no metric can be computed.

    `security` is its row of `Store.read_security` and `facts` its facts, which
    came from `facts_source`. `percentiles` gives, per metric, the percentile in
    the universe and in the sector as of the date. `valuation` is what its
    price implies and `risk` what it behaves like, where those were worked out.
    """
    found = statements(facts, as_of)
    if not found:
        return None
    return TickerReport(
        ticker=ticker,
        universe=universe,
        as_of=pd.Timestamp(as_of).date(),
        facts_source=facts_source,
        name=security["name"],
        cik=int(security["cik"]),
        sic=security["sic"],
        sic_description=security["sic_description"],
        sector=None if pd.isna(security["sector"]) else security["sector"],
        series=[
            Series(
                metric=name,
                group=METRICS[name].group,
                label=METRICS[name].label,
                unit=METRICS[name].unit,
                pct_universe=percentiles.get(name, (None, None))[0],
                pct_sector=percentiles.get(name, (None, None))[1],
                points=points,
            )
            for name, points in found.items()
        ],
        valuation=valuation,
        risk=risk,
    )


def _day(days: int) -> date:
    return date.fromordinal(_UNIX_EPOCH + days)


class _Book:
    """The facts known on a date, one per period, by concept."""

    def __init__(self, facts: pd.DataFrame, as_of: pd.Timestamp) -> None:
        known = facts[facts["filed"] < as_of].copy()
        known["start"] = known["start"].fillna(known["end"])
        # The filing of a figure is the first that reported its value.
        first = known.sort_values(
            ["concept", "start", "end", "value", "filed", "priority"]
        ).drop_duplicates(["concept", "start", "end", "value"])
        self._concepts: list[str] = first["concept"].tolist()
        self._starts = days_since_epoch(first["start"])
        self._ends = days_since_epoch(first["end"])
        self._filed = days_since_epoch(first["filed"])
        self._priorities: list[int] = first["priority"].tolist()
        self._filing: list[tuple[Any, ...]] = list(
            zip(
                *(first[c].tolist() for c in ("accn", "form", "fy", "fp", "cik")),
                strict=True,
            )
        )
        first_row = {
            key: row
            for row, key in enumerate(
                zip(
                    self._concepts,
                    self._starts,
                    self._ends,
                    first["value"].tolist(),
                    strict=True,
                )
            )
        }
        # The value of a period is the latest filed, the highest-ranked tag on
        # a tie, as the model's features read it.
        latest = known.sort_values(
            ["concept", "start", "end", "filed", "priority"],
            ascending=[True, True, True, True, False],
        ).drop_duplicates(["concept", "start", "end"], keep="last")
        self._source: list[int] = []
        self._facts: dict[str, list[Fact]] = {}
        periods = zip(
            latest["concept"].tolist(),
            days_since_epoch(latest["start"]),
            days_since_epoch(latest["end"]),
            latest["value"].tolist(),
            strict=True,
        )
        for concept, start, end, value in periods:
            self._source.append(first_row[concept, start, end, value])
            self._facts.setdefault(concept, []).append(
                Fact(start, end, float(value), len(self._source) - 1)
            )

    def part(self, name: str, sign: int, fact: Fact) -> Part:
        row = self._source[fact.ref]
        accn, form, fy, fp, cik = self._filing[row]
        start, end = self._starts[row], self._ends[row]
        return Part(
            metric=name,
            sign=1 if sign > 0 else -1,
            tag=CONCEPTS[self._concepts[row]].tags[self._priorities[row]],
            start=None if start == end else _day(start),
            end=_day(end),
            value=fact.value,
            filing=Filing(
                accn=accn,
                form=form,
                fy=int(fy),
                fp=fp,
                filed=_day(self._filed[row]),
                url=filing_url(int(cik), accn),
            ),
        )

    def quantity(self, name: str) -> _Quantity:
        """Return a quantity's readable ends and pieces."""
        if name == "debt":
            return self._debt()
        facts = self._facts.get(name, [])
        kind = CONCEPTS[name].kind
        if kind == "flow":
            cumulative = {fact.end for fact in facts if is_cumulative(fact)}
            return _Quantity(
                name, cumulative, cache(lambda end: ttm_pieces(facts, end))
            )
        if kind == "average":
            return self._average(name, facts)
        return self._by_end(name, {fact.end: [(1, fact)] for fact in facts})

    @staticmethod
    def _by_end(name: str, found: dict[int, _Pieces]) -> _Quantity:
        return _Quantity(name, set(found), found.get)

    def _average(self, name: str, facts: list[Fact]) -> _Quantity:
        """The longest period that ends on each date: a weighted average does
        not add up across periods."""
        longest: dict[int, Fact] = {}
        for fact in facts:
            if is_cumulative(fact) and (
                fact.end not in longest or span(fact) > span(longest[fact.end])
            ):
                longest[fact.end] = fact
        return self._by_end(name, {end: [(1, fact)] for end, fact in longest.items()})

    def _debt(self) -> _Quantity:
        """Long-term debt plus the current portion reported for the same date. A
        company that reports no current debt then is taken to have none."""
        current = {fact.end: fact for fact in self._facts.get("current_debt", [])}
        found: dict[int, _Pieces] = {}
        for fact in self._facts.get("long_term_debt", []):
            pieces = [(1, fact)]
            if fact.end in current:
                pieces.append((1, current[fact.end]))
            found[fact.end] = pieces
        return self._by_end("debt", found)
