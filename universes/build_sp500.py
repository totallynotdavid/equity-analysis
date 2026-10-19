"""Builds universes/sp500.txt, the point-in-time S&P 500 membership.

Run it from the repository root: `uv run python universes/build_sp500.py`.

Wikipedia's "List of S&P 500 companies" lists today's members. Its "Historical
components of the S&P 500" holds a table of every change: the date, the ticker
added and the ticker removed, either of which can be empty. The script starts
from today's members and undoes the changes from newest to oldest. A name added
on a date was a member from then on, and a name removed was a member until then.
Whoever is still a member when the walk reaches `--since` was a member since
that date, as far as the table says.

Beside it the script writes `sp500.companies.csv`: the SEC identifier (CIK) of
every ticker in the file and the GICS sector of today's members. The filings
source takes the identifier from it and the ticker pages show the sector. A
current member's identifier is in the SEC's `company_tickers.json` and its
sector in the constituents table. A current member the SEC file does not list
has no identifier and is left out; the script names it. `PREDECESSORS` adds the
earlier registrants of a company that moved to a new one. A name that
left is no longer in the SEC's ticker list, and its ticker may name another
company now, so its identifier comes from `sp500.former.csv`: a reviewed table
of the changes table's `Security` name and the CIK of that company, with the
ticker where two companies share a name. A former member whose name is not in
it has no identifier and no filings; the script lists them.

The SEC wants a declared `User-Agent` on every request. The SEC file is fetched
with the `SEC_USER_AGENT` environment variable, which `mise.toml` sets, and with
nothing else; the script stops when it is unset.

Wikipedia's text is licensed CC BY-SA 4.0. The outputs carry the page URLs and
the fetch date, and the same licence.
"""

import argparse
import csv
import json
import os
import re
import sys

from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path

import httpx2


CURRENT_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
CHANGES_URL = "https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
LICENCE = "https://creativecommons.org/licenses/by-sa/4.0/"
USER_AGENT = (
    "equity-analysis-universe-builder/1.0 "
    "(https://github.com/totallynotdavid/equity-analysis)"
)
# A company that moved to a new registrant keeps the history of its filings under
# the old one, and neither EDGAR nor the table links the two. The old CIK follows
# the new one in the companies file. XOM moved to ExxonMobil Holdings Corp in 2026.
PREDECESSORS: dict[str, tuple[int, ...]] = {"XOM": (34088,)}
DEFAULT_OUT = Path(__file__).with_name("sp500.txt")
DEFAULT_FORMER = Path(__file__).with_name("sp500.former.csv")
# The default starts in the first year with at least 16 rows in the changes
# table. A member that joined earlier starts on this date.
DEFAULT_SINCE = date(2011, 1, 1)


@dataclass(frozen=True)
class Change:
    effective: date
    added: str | None
    removed: str | None
    removed_name: str = ""


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


def sec_ciks(text: str) -> dict[str, int]:
    """The CIK of each ticker in the SEC's `company_tickers.json`, whose entries
    look like `{"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}`."""
    ciks: dict[str, int] = {}
    for entry in json.loads(text).values():
        ticker = normalise(str(entry["ticker"]))
        if ticker:
            ciks[ticker] = int(entry["cik_str"])
    return ciks


def current_companies(
    rows: list[list[str]], ciks: dict[str, int]
) -> tuple[dict[str, tuple[int, str]], list[str]]:
    """Each current ticker with its SEC identifier and GICS sector, and the
    tickers the SEC file does not list.

    The identifier is the ticker's in `ciks`. The sector is the third column of
    the constituents table. A member the SEC file lacks is not kept, and neither
    is a row without a sector.
    """
    companies: dict[str, tuple[int, str]] = {}
    unlisted: list[str] = []
    for row in rows:
        ticker = normalise(row[0])
        if not ticker or not row[2]:
            continue
        if ticker in ciks:
            companies[ticker] = (ciks[ticker], row[2])
        else:
            unlisted.append(ticker)
    return companies, sorted(unlisted)


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
            changes.append(Change(effective, added, removed, " ".join(row[4].split())))
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


def former_securities(spans: list[Span], changes: list[Change]) -> dict[str, str]:
    """The changes table's `Security` name of each ticker that is no longer a
    member, from the row that removed it last."""
    names = {(c.removed, c.effective): c.removed_name for c in changes if c.removed}
    former: dict[str, str] = {}
    for span in spans:
        if span.end is None:
            former.pop(span.ticker, None)
        else:
            former[span.ticker] = names[span.ticker, span.end]
    return former


def read_former(path: Path) -> dict[tuple[str, str], str]:
    """The reviewed table, one `security,cik` or `security,cik,ticker` per line,
    keyed by the name and the ticker (empty when the line has none). A company
    that moved to a new registrant lists the new CIK first and the earlier ones
    after it, joined with `+`. Two companies that share a name are told apart by
    the ticker. `#` starts a comment line."""
    table: dict[tuple[str, str], str] = {}
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = next(csv.reader([line]))
        if (
            len(fields) not in (2, 3)
            or not fields[0].strip()
            or not all(cik.isdigit() for cik in fields[1].strip().split("+"))
        ):
            raise ValueError(
                f"{path}:{number}: expected `security,cik[,ticker]`, got `{line}`"
            )
        ticker = fields[2].strip() if len(fields) == 3 else ""
        table[" ".join(fields[0].split()), ticker] = fields[1].strip()
    return table


def former_ciks(
    names: dict[str, str], table: dict[tuple[str, str], str]
) -> dict[str, str]:
    """The CIK of each former ticker the table has: the line for its name and
    ticker, else the line for its name alone."""
    found = {}
    for ticker, name in names.items():
        cik = table.get((name, ticker)) or table.get((name, ""))
        if cik:
            found[ticker] = cik
    return found


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


def render_companies(
    companies: dict[str, tuple[int, str]], former: dict[str, str], fetched: date
) -> str:
    """The companies file: today's members with their sector, then the former
    members that have a CIK, with no sector."""
    rows = {
        ticker: ("+".join(str(c) for c in (cik, *PREDECESSORS.get(ticker, ()))), sector)
        for ticker, (cik, sector) in companies.items()
    }
    rows.update({t: (cik, "") for t, cik in former.items() if t not in rows})
    lines = [
        "# SEC identifier of every ticker in sp500.txt that has one and the current "
        "GICS sector of the members, generated by universes/build_sp500.py. Do not "
        "edit.",
        f"# Source: {SEC_TICKERS_URL} and {CURRENT_URL}, fetched {fetched}, and "
        "sp500.former.csv.",
        f"# Wikipedia text is CC BY-SA 4.0: {LICENCE}",
        "# Format: ticker,cik,sector. A name that has left the index has no sector. "
        "A ticker not listed has no identifier.",
    ]
    lines.extend(
        f"{ticker},{cik},{sector}" for ticker, (cik, sector) in sorted(rows.items())
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
    parser.add_argument("--former", type=Path, default=DEFAULT_FORMER)
    parser.add_argument("--user-agent", default=USER_AGENT)
    args = parser.parse_args(argv)
    sec_user_agent = os.environ.get("SEC_USER_AGENT")
    if not sec_user_agent:
        parser.error("SEC_USER_AGENT is not set; the SEC refuses a request without one")

    constituents = read_tables(fetch(CURRENT_URL, args.user_agent), {"constituents"})[
        "constituents"
    ]
    current = current_members(constituents)
    changes, problems = parse_changes(
        read_tables(fetch(CHANGES_URL, args.user_agent), {"changes"})["changes"]
    )
    spans, walk_problems = build_spans(current, changes, args.since)
    args.out.write_text(render(spans, args.since, date.today()))
    companies, unlisted = current_companies(
        constituents, sec_ciks(fetch(SEC_TICKERS_URL, sec_user_agent))
    )
    names = former_securities(spans, changes)
    table = read_former(args.former)
    former = former_ciks(names, table)
    unresolved = {t: n for t, n in names.items() if t not in former}
    companies_out = args.out.with_suffix(".companies.csv")
    companies_out.write_text(render_companies(companies, former, date.today()))

    sys.stderr.write(
        f"wrote {len(spans)} spans for {len({s.ticker for s in spans})} tickers "
        f"to {args.out}; {len(current)} current members, {len(changes)} changes\n"
        f"wrote {len(companies)} current and {len(former)} former companies to "
        f"{companies_out}\n"
    )
    if unlisted:
        sys.stderr.write(
            f"{len(unlisted)} current members are not in the SEC's ticker file and "
            f"have no CIK: {', '.join(unlisted)}\n"
        )
    if unresolved:
        sys.stderr.write(
            f"{len(unresolved)} former members have no CIK in {args.former}: "
            + ", ".join(f"{t} ({n})" for t, n in sorted(unresolved.items()))
            + "\n"
        )
    used = {(name, t) for t, name in names.items()} | {
        (name, "") for name in names.values()
    }
    unused = sorted(name for name, ticker in set(table) - used)
    if unused:
        sys.stderr.write(
            f"warning: {args.former} names no former member: {', '.join(unused)}\n"
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
