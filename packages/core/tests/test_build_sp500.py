import importlib.util
import sys

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from index_core.universe import read_universe


if TYPE_CHECKING:
    from types import ModuleType


def _load() -> ModuleType:
    path = Path(__file__).parents[3] / "universes" / "build_sp500.py"
    spec = importlib.util.spec_from_file_location("build_sp500", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_sp500"] = module
    spec.loader.exec_module(module)
    return module


build = _load()

# The shape of the two Wikipedia tables: a two-row header with spans, footnote
# marks in <sup>, empty cells where a change only adds or only removes, and a
# stray `|` after a ticker.
CURRENT_PAGE = """
<table class="wikitable"><tr><th>Other</th></tr><tr><td>NOTME</td></tr></table>
<table class="wikitable sortable" id="constituents">
<tr><th>Symbol</th><th>Security</th><th>Sector</th><th>Sub</th><th>HQ</th>
<th>Date added</th><th>CIK</th><th>Founded</th></tr>
<tr><td><a href="x">AAA</a></td><td>Aaa Inc</td><td>s</td><td>s</td><td>h</td>
<td>1957-03-04</td><td>1</td><td>1902</td></tr>
<tr><td>BRK.B</td><td>Berkshire</td><td>s</td><td>s</td><td>h</td>
<td>2010-02-16</td><td>2</td><td>1839</td></tr>
<tr><td>NEWT</td><td>Newtick</td><td>s</td><td>s</td><td>h</td>
<td>2015-03-01</td><td>3</td><td>1990</td></tr>
<tr><td>BACK</td><td>Back Inc</td><td>s</td><td>s</td><td>h</td>
<td>unknown</td><td>4</td><td>1990</td></tr>
<tr><td>ADDED</td><td>Added Inc</td><td>s</td><td>s</td><td>h</td>
<td>2020-05-01</td><td>5</td><td>1990</td></tr>
</table>
"""

CHANGES_PAGE = """
<table class="wikitable sortable" id="changes"><tbody>
<tr><th rowspan="2">Effective Date</th><th colspan="2">Added</th>
<th colspan="2">Removed</th><th rowspan="2">Reason</th><th rowspan="2">Refs</th></tr>
<tr><th>Ticker</th><th>Security</th><th>Ticker</th><th>Security</th></tr>
<tr><td>May 1, 2020</td><td>ADDED</td><td><a>Added Inc</a></td>
<td>GONE</td><td><a>Gone Corp</a></td><td>Market cap.</td>
<td><sup><a>[1]</a></sup></td></tr>
<tr><td rowspan="2">March 2, 2018</td><td></td><td></td><td>CLOSED</td>
<td>Closed Inc</td><td>Acquired</td><td></td></tr>
<tr><td>BACK</td><td>Back Inc</td><td>OLD |</td><td>Old Inc</td><td>Swap</td><td></td></tr>
<tr><td>March 1, 2015</td><td>NEWT</td><td>Newtick</td><td>FORMER</td>
<td>Former Inc</td><td>Swap</td><td></td></tr>
<tr><td>January 15, 2013</td><td>RENAMED</td><td>Renamed</td><td>ANCIENT</td>
<td>Ancient Inc</td><td>Swap</td><td></td></tr>
<tr><td>sometime in 2012</td><td>X</td><td>X</td><td>Y</td><td>Y</td><td></td><td></td></tr>
</tbody></table>
"""


def test_a_table_is_read_by_id_with_its_spans_and_without_footnote_marks() -> None:
    rows = build.read_tables(CHANGES_PAGE, {"changes"})["changes"]

    assert rows[0] == [
        "May 1, 2020",
        "ADDED",
        "Added Inc",
        "GONE",
        "Gone Corp",
        "Market cap.",
        "",
    ]
    assert rows[1][0] == rows[2][0] == "March 2, 2018"
    assert len(rows) == 6


def test_a_page_without_the_table_is_an_error() -> None:
    with pytest.raises(ValueError, match="no table with id"):
        build.read_tables("<table id='other'></table>", {"changes"})


def test_current_members_carry_their_date_added_and_dots_become_dashes() -> None:
    rows = build.read_tables(CURRENT_PAGE, {"constituents"})["constituents"]

    assert build.current_members(rows) == {
        "AAA": date(1957, 3, 4),
        "BRK-B": date(2010, 2, 16),
        "NEWT": date(2015, 3, 1),
        "BACK": None,
        "ADDED": date(2020, 5, 1),
    }


def test_changes_are_read_and_an_unreadable_row_is_reported() -> None:
    rows = build.read_tables(CHANGES_PAGE, {"changes"})["changes"]

    changes, problems = build.parse_changes(rows)

    assert changes[0] == build.Change(date(2020, 5, 1), "ADDED", "GONE")
    assert build.Change(date(2018, 3, 2), None, "CLOSED") in changes
    assert build.Change(date(2018, 3, 2), "BACK", "OLD") in changes
    assert len(changes) == 5
    assert problems == [
        "unreadable date in row ['sometime in 2012', 'X', 'X', 'Y', 'Y']"
    ]


def _spans(since: date) -> tuple[list[Any], list[str]]:
    current = build.current_members(
        build.read_tables(CURRENT_PAGE, {"constituents"})["constituents"]
    )
    changes, _ = build.parse_changes(
        build.read_tables(CHANGES_PAGE, {"changes"})["changes"]
    )
    return cast(
        "tuple[list[Any], list[str]]", build.build_spans(current, changes, since)
    )


def test_the_walk_back_from_today_gives_each_name_its_membership_span() -> None:
    spans, problems = _spans(date(2012, 1, 1))

    by_ticker = {(s.ticker, s.start, s.end) for s in spans}
    assert by_ticker == {
        # Members before the window that the table never touches.
        ("AAA", date(2012, 1, 1), None),
        ("BRK-B", date(2012, 1, 1), None),
        # Removed inside the window, so members from its start until then.
        ("GONE", date(2012, 1, 1), date(2020, 5, 1)),
        ("CLOSED", date(2012, 1, 1), date(2018, 3, 2)),
        ("OLD", date(2012, 1, 1), date(2018, 3, 2)),
        ("FORMER", date(2012, 1, 1), date(2015, 3, 1)),
        ("ANCIENT", date(2012, 1, 1), date(2013, 1, 15)),
        # Added inside the window and still members.
        ("ADDED", date(2020, 5, 1), None),
        ("BACK", date(2018, 3, 2), None),
        ("NEWT", date(2015, 3, 1), None),
    }
    assert problems == ["2013-01-15: RENAMED was added but is not a member afterwards"]


def test_a_change_before_the_start_date_is_ignored() -> None:
    spans, problems = _spans(date(2016, 1, 1))

    assert {(s.ticker, s.start, s.end) for s in spans if s.ticker == "NEWT"} == {
        ("NEWT", date(2016, 1, 1), None)
    }
    assert {"FORMER", "ANCIENT", "RENAMED"}.isdisjoint(s.ticker for s in spans)
    assert problems == []


def test_a_member_with_no_change_row_starts_on_its_date_added_when_that_is_later() -> (
    None
):
    current = {"RENAMED": date(2014, 6, 2), "OLDER": date(1990, 1, 1), "BLANK": None}

    spans, problems = build.build_spans(current, [], date(2011, 1, 1))

    assert {(s.ticker, s.start) for s in spans} == {
        ("RENAMED", date(2014, 6, 2)),
        ("OLDER", date(2011, 1, 1)),
        ("BLANK", date(2011, 1, 1)),
    }
    assert problems == []


def test_a_removal_of_a_name_that_is_a_member_again_is_reported_and_skipped() -> None:
    current: dict[str, date | None] = {}
    changes = [
        build.Change(date(2020, 1, 1), None, "REUSED"),
        build.Change(date(2015, 1, 1), None, "REUSED"),
    ]

    spans, problems = build.build_spans(current, changes, date(2011, 1, 1))

    assert spans == [build.Span("REUSED", date(2011, 1, 1), date(2020, 1, 1))]
    assert problems == [
        "2015-01-01: REUSED was removed but is a member again with no addition "
        "in between"
    ]


def test_the_generated_file_loads_and_names_its_source_and_licence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages = {build.CURRENT_URL: CURRENT_PAGE, build.CHANGES_URL: CHANGES_PAGE}
    agents: list[str] = []

    def fetch(url: str, user_agent: str) -> str:
        agents.append(user_agent)
        return pages[url]

    monkeypatch.setattr(build, "fetch", fetch)
    out = tmp_path / "sp500.txt"

    build.main(
        ["--out", str(out), "--since", "2012-01-01", "--user-agent", "test-agent"]
    )

    text = out.read_text()
    assert agents == ["test-agent", "test-agent"]
    assert build.CHANGES_URL in text
    assert build.CURRENT_URL in text
    assert f"fetched {date.today()}." in text
    assert "CC BY-SA 4.0" in text
    universe = read_universe(out)
    assert universe.members_on(date(2019, 1, 1)) == [
        "AAA",
        "BACK",
        "BRK-B",
        "GONE",
        "NEWT",
    ]
    assert universe.members_on(date(2013, 6, 1)) == [
        "AAA",
        "BRK-B",
        "CLOSED",
        "FORMER",
        "GONE",
        "OLD",
    ]
