"""The fundamentals of one company with the filed figure behind every value.

`statements` evaluates `metrics.METRICS` on the facts known on a date. A trailing
twelve months comes from the same `periods.ttm_pieces` the model's features
use. Each point lists the figures it is made of, and each figure names the
filing it first appeared in.
"""

from typing import TYPE_CHECKING, Any, NamedTuple

import numpy as np
import pandas as pd

from index_core.metrics import METRICS, evaluate
from index_core.periods import (
    Fact,
    cumulative_ends,
    is_cumulative,
    span,
    ttm_pieces,
)
from index_core.report import Filing, Part, Point, Series, TickerReport
from index_core.sources.edgar import CONCEPTS, filing_url


if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

# Keep three years of quarterly points for each metric.
POINTS = 12

_EPOCH = pd.Timestamp(0)


class _Row(NamedTuple):
    tag: str
    start: pd.Timestamp | None
    end: pd.Timestamp
    value: float
    filing: Filing


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
    quantities: dict[str, dict[int, list[Part]]] = {}

    def quantity(name: str) -> dict[int, list[Part]]:
        if name not in quantities:
            quantities[name] = book.quantity(name)
        return quantities[name]

    result: dict[str, list[Point]] = {}
    for name, metric in METRICS.items():
        inputs = [quantity(i) for i in metric.inputs]
        ends = sorted(set.intersection(*(set(i) for i in inputs)))
        found: list[Point] = []
        for end in ends:
            parts = [part for i in inputs for part in i[end]]
            # Filings print at most four decimals; rounding drops the float
            # noise of adding and subtracting them.
            values = [
                round(sum(part.sign * part.value for part in i[end]), 6) for i in inputs
            ]
            value = float(evaluate(metric, values))
            if np.isfinite(value):
                found.append(
                    Point(
                        end=_day(end),
                        value=value,
                        formula=metric.formula,
                        parts=parts,
                    )
                )
        if found:
            result[name] = found[-points:]
    return result


def ticker_report(
    ticker: str,
    universe: str,
    as_of: date | pd.Timestamp,
    facts_source: str,
    security: Mapping[str, Any],
    facts: pd.DataFrame,
    percentiles: Mapping[str, tuple[float | None, float | None]],
) -> TickerReport | None:
    """The report of one ticker, or None where no metric can be computed.

    `security` is its row of `Store.read_security` and `facts` its facts, which
    came from `facts_source`. `percentiles` gives, per metric, the percentile in
    the universe and in the sector as of the date.
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
    )


def _day(days: int) -> date:
    return (_EPOCH + pd.Timedelta(days=days)).date()


def _days(timestamp: pd.Timestamp) -> int:
    return (timestamp - _EPOCH).days


class _Book:
    """The facts known on a date, one per period, by concept."""

    def __init__(self, facts: pd.DataFrame, as_of: pd.Timestamp) -> None:
        known = facts[facts["filed"] < as_of].copy()
        known["start"] = known["start"].fillna(known["end"])
        # The filing of a figure is the first that reported its value.
        first = known.sort_values(
            ["concept", "start", "end", "value", "filed", "priority"]
        ).drop_duplicates(["concept", "start", "end", "value"])
        firsts: list[Any] = list(first.itertuples())
        self._first = {
            (row.concept, row.start, row.end, row.value): row for row in firsts
        }
        # The value of a period is the latest filed, the highest-ranked tag on
        # a tie, as the model's features read it.
        latest = known.sort_values(
            ["concept", "start", "end", "filed", "priority"],
            ascending=[True, True, True, True, False],
        ).drop_duplicates(["concept", "start", "end"], keep="last")
        self._rows: list[_Row] = []
        self._facts: dict[str, list[Fact]] = {}
        periods: list[Any] = list(latest.itertuples())
        for row in periods:
            source = self._first[row.concept, row.start, row.end, row.value]
            self._rows.append(self._row(source))
            self._facts.setdefault(row.concept, []).append(
                Fact(
                    _days(row.start),
                    _days(row.end),
                    float(row.value),
                    len(self._rows) - 1,
                )
            )

    def _row(self, source: Any) -> _Row:
        return _Row(
            tag=CONCEPTS[source.concept].tags[source.priority],
            start=None if source.start == source.end else source.start,
            end=source.end,
            value=float(source.value),
            filing=Filing(
                accn=source.accn,
                form=source.form,
                fy=int(source.fy),
                fp=source.fp,
                filed=source.filed.date(),
                url=filing_url(int(source.cik), source.accn),
            ),
        )

    def _part(self, name: str, sign: int, fact: Fact) -> Part:
        row = self._rows[fact.ref]
        return Part(
            metric=name,
            sign=1 if sign > 0 else -1,
            tag=row.tag,
            start=None if row.start is None else row.start.date(),
            end=row.end.date(),
            value=row.value,
            filing=row.filing,
        )

    def quantity(self, name: str) -> dict[int, list[Part]]:
        """The parts of a quantity at each period end it can be read for."""
        if name == "debt":
            return self._debt()
        facts = self._facts.get(name, [])
        kind = CONCEPTS[name].kind
        if kind == "flow":
            return self._flow(name, facts)
        if kind == "average":
            return self._average(name, facts)
        return {fact.end: [self._part(name, 1, fact)] for fact in facts}

    def _flow(self, name: str, facts: list[Fact]) -> dict[int, list[Part]]:
        found: dict[int, list[Part]] = {}
        for end in cumulative_ends(facts):
            pieces = ttm_pieces(facts, end)
            if pieces is not None:
                found[end] = [self._part(name, sign, fact) for sign, fact in pieces]
        return found

    def _average(self, name: str, facts: list[Fact]) -> dict[int, list[Part]]:
        """The longest period that ends on each date: a weighted average does
        not add up across periods."""
        longest: dict[int, Fact] = {}
        for fact in facts:
            if is_cumulative(fact) and (
                fact.end not in longest or span(fact) > span(longest[fact.end])
            ):
                longest[fact.end] = fact
        return {end: [self._part(name, 1, fact)] for end, fact in longest.items()}

    def _debt(self) -> dict[int, list[Part]]:
        """Long-term debt plus the current portion reported for the same date. A
        company that reports no current debt then is taken to have none."""
        current = {fact.end: fact for fact in self._facts.get("current_debt", [])}
        found: dict[int, list[Part]] = {}
        for fact in self._facts.get("long_term_debt", []):
            parts = [self._part("debt", 1, fact)]
            if fact.end in current:
                parts.append(self._part("debt", 1, current[fact.end]))
            found[fact.end] = parts
        return found
