"""Builds universes/sp500.txt, the point-in-time S&P 500 membership.

Run it from the repository root: `uv run python universes/build_sp500.py`.

Wikipedia's "List of S&P 500 companies" lists today's members. Its "Historical
components of the S&P 500" holds a table of every change: the date, the ticker
added and the ticker removed, either of which can be empty. The script starts
from today's members and undoes the changes from newest to oldest. A name added
on a date was a member from then on, and a name removed was a member until then.
Whoever is still a member when the walk reaches `--since` was a member since
that date, as far as the table says.

Wikipedia's text is licensed CC BY-SA 4.0. The output carries the page URLs and
the fetch date, and the same licence.
"""

import argparse
import re
import sys

from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path

import httpx2


CURRENT_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
CHANGES_URL = "https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500"
LICENCE = "https://creativecommons.org/licenses/by-sa/4.0/"
USER_AGENT = (
    "equity-analysis-universe-builder/1.0 "
    "(https://github.com/totallynotdavid/equity-analysis)"
)
DEFAULT_OUT = Path(__file__).with_name("sp500.txt")
# The default starts in the first year with at least 16 rows in the changes
# table. A member that joined earlier starts on this date.
DEFAULT_SINCE = date(2011, 1, 1)


@dataclass(frozen=True)
class Change:
    effective: date
    added: str | None
    removed: str | None


@dataclass(frozen=True)
class Span:
    ticker: str
    start: date
    end: date | None


class _TableReader(HTMLParser):
    """Collects the cells of the tables whose `id` is in `wanted`.

    A cell with `rowspan` or `colspan` repeats in every position it covers. The
    text of `<sup>` footnote marks is dropped. A row with no `<td>`, such as a
    header, is not kept.
    """

    def __init__(self, wanted: set[str]) -> None:
        super().__init__()
        self.tables: dict[str, list[list[str]]] = {}
        self._wanted = wanted
        self._table: list[list[str]] | None = None
        self._depth = 0
        self._row: list[str] = []
        self._has_data = False
        self._carry: dict[int, tuple[int, str]] = {}
        self._cell: list[str] | None = None
        self._span = (1, 1)
        self._sup = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "table":
            if self._table is None and values.get("id") in self._wanted:
                self._table = self.tables.setdefault(str(values["id"]), [])
            if self._table is not None:
                self._depth += 1
        elif self._table is None:
            return
        elif tag == "tr":
            self._row, self._has_data = [], False
        elif tag in ("td", "th"):
            self._cell = []
            self._has_data = self._has_data or tag == "td"
            self._span = (_span(values.get("rowspan")), _span(values.get("colspan")))
        elif tag == "sup":
            self._sup += 1

    def handle_endtag(self, tag: str) -> None:
        if self._table is None:
            return
        if tag == "table":
            self._depth -= 1
            if self._depth == 0:
                self._table = None
        elif tag in ("td", "th") and self._cell is not None:
            text = re.sub(r"\s+", " ", "".join(self._cell)).strip()
            self._place(text, *self._span)
            self._cell = None
        elif tag == "tr":
            self._fill()
            if self._has_data:
                self._table.append(self._row)
        elif tag == "sup":
            self._sup -= 1

    def handle_data(self, data: str) -> None:
        if self._cell is not None and not self._sup:
            self._cell.append(data)

    def _fill(self) -> None:
        while len(self._row) in self._carry:
            remaining, text = self._carry.pop(len(self._row))
            if remaining > 1:
                self._carry[len(self._row)] = (remaining - 1, text)
            self._row.append(text)

    def _place(self, text: str, rows: int, columns: int) -> None:
        self._fill()
        for _ in range(columns):
            if rows > 1:
                self._carry[len(self._row)] = (rows - 1, text)
            self._row.append(text)
            self._fill()


def _span(value: str | None) -> int:
    return int(value) if value and value.isdigit() else 1


def read_tables(html: str, wanted: set[str]) -> dict[str, list[list[str]]]:
    reader = _TableReader(wanted)
    reader.feed(html)
    reader.close()
    missing = wanted - set(reader.tables)
    if missing:
        raise ValueError(f"the page has no table with id {sorted(missing)}")
    return reader.tables


def normalise(ticker: str) -> str | None:
    """A ticker as Tiingo and EDGAR write it: `BRK.B` is `BRK-B`.

    Some cells carry a stray `|` after the symbol, so only the first word counts.
    """
    words = ticker.upper().replace(".", "-").split()
    return words[0] if words else None


def current_members(rows: list[list[str]]) -> dict[str, date | None]:
    """Each current ticker with its "Date added", or None where that is not a date.

    Columns are the symbol, the name, two GICS columns, the headquarters and the
    date added.
    """
    members: dict[str, date | None] = {}
    for row in rows:
        ticker = normalise(row[0])
        if ticker:
            try:
                members[ticker] = date.fromisoformat(row[5])
            except ValueError:
                members[ticker] = None
    return members


def parse_changes(rows: list[list[str]]) -> tuple[list[Change], list[str]]:
    """The changes table as changes, and a message for each row it cannot read.

    A row is the effective date, then the ticker and name added, then the ticker
    and name removed. The columns after those are not read.
    """
    changes: list[Change] = []
    problems: list[str] = []
    for row in rows:
        try:
            effective = datetime.strptime(row[0], "%B %d, %Y").date()
        except ValueError, IndexError:
            problems.append(f"unreadable date in row {row[:5]}")
            continue
        if len(row) < 5:
            problems.append(f"short row {row}")
            continue
        added, removed = normalise(row[1]), normalise(row[3])
        if added or removed:
            changes.append(Change(effective, added, removed))
    return changes, problems


def build_spans(
    current: dict[str, date | None], changes: list[Change], since: date
) -> tuple[list[Span], list[str]]:
    """Undo `changes` from newest to oldest, starting from the current members.

    Returns the spans and a message for each change the walk cannot explain.
    Such a change is skipped.

    A current member the walk never sees added starts on `since`, unless its
    "Date added" is later. That is a company whose ticker changed after it
    joined: the table lists the old ticker, today's list the new one.
    """
    open_until: dict[str, date | None] = dict.fromkeys(current)
    spans: list[Span] = []
    problems: list[str] = []
    for change in sorted(
        (c for c in changes if c.effective >= since),
        key=lambda c: c.effective,
        reverse=True,
    ):
        if change.added:
            if change.added in open_until:
                spans.append(
                    Span(change.added, change.effective, open_until.pop(change.added))
                )
            else:
                problems.append(
                    f"{change.effective}: {change.added} was added but is not a "
                    "member afterwards"
                )
        if change.removed:
            if change.removed in open_until:
                problems.append(
                    f"{change.effective}: {change.removed} was removed but is a "
                    "member again with no addition in between"
                )
            else:
                open_until[change.removed] = change.effective
    for ticker, end in open_until.items():
        added = current.get(ticker) if end is None else None
        spans.append(Span(ticker, max(since, added) if added else since, end))
    return sorted(spans, key=lambda s: (s.ticker, s.start)), problems


def changes_per_year(changes: list[Change]) -> dict[int, int]:
    """Rows of the changes table per year. A year with few is probably incomplete."""
    counts: dict[int, int] = {}
    for change in changes:
        counts[change.effective.year] = counts.get(change.effective.year, 0) + 1
    return dict(sorted(counts.items()))


def render(spans: list[Span], since: date, fetched: date) -> str:
    lines = [
        "# S&P 500 membership, generated by universes/build_sp500.py. Do not edit.",
        f"# Source: {CHANGES_URL}",
        f"# and {CURRENT_URL}, fetched {fetched}.",
        f"# Wikipedia text is CC BY-SA 4.0: {LICENCE}",
        f"# Dates start on {since}; a member that joined earlier starts on it.",
        "# Format: ticker,start[,end]. A blank end means still a member.",
    ]
    lines.extend(
        f"{s.ticker},{s.start}," + (str(s.end) if s.end else "") for s in spans
    )
    return "\n".join(lines) + "\n"


def fetch(url: str, user_agent: str) -> str:
    response = httpx2.get(
        url, headers={"User-Agent": user_agent}, timeout=60.0, follow_redirects=True
    )
    response.raise_for_status()
    return response.text


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--since", type=date.fromisoformat, default=DEFAULT_SINCE)
    parser.add_argument("--user-agent", default=USER_AGENT)
    args = parser.parse_args(argv)

    current = current_members(
        read_tables(fetch(CURRENT_URL, args.user_agent), {"constituents"})[
            "constituents"
        ]
    )
    changes, problems = parse_changes(
        read_tables(fetch(CHANGES_URL, args.user_agent), {"changes"})["changes"]
    )
    spans, walk_problems = build_spans(current, changes, args.since)
    args.out.write_text(render(spans, args.since, date.today()))

    sys.stderr.write(
        f"wrote {len(spans)} spans for {len({s.ticker for s in spans})} tickers "
        f"to {args.out}; {len(current)} current members, {len(changes)} changes\n"
    )
    for problem in [*problems, *walk_problems]:
        sys.stderr.write(f"warning: {problem}\n")
    sys.stderr.write(
        "changes per year: "
        + ", ".join(f"{y}: {n}" for y, n in changes_per_year(changes).items())
        + "\n"
    )


if __name__ == "__main__":
    main()
