from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from index_core.universe import Membership, read_universe


UNIVERSES = Path(__file__).parents[3] / "universes"


def _file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "universe.txt"
    path.write_text(text)
    return path


def test_reads_memberships_skipping_comments_and_blank_lines(tmp_path: Path) -> None:
    path = _file(
        tmp_path,
        "# demo\nAAPL,2010-01-04\n\n  msft , 2011-02-01 , 2015-06-01  # left\n"
        "NVDA,2012-01-03,\n",
    )

    universe = read_universe(path)

    assert universe.name == "universe"
    assert universe.memberships == (
        Membership("AAPL", date(2010, 1, 4), None),
        Membership("MSFT", date(2011, 2, 1), date(2015, 6, 1)),
        Membership("NVDA", date(2012, 1, 3), None),
    )
    assert universe.tickers == ["AAPL", "MSFT", "NVDA"]


def test_a_name_is_a_member_from_its_start_through_the_day_before_its_end(
    tmp_path: Path,
) -> None:
    universe = read_universe(
        _file(tmp_path, "OLD,2010-01-04,2015-06-01\nNEW,2015-06-01\n")
    )

    assert universe.members_on(date(2010, 1, 3)) == []
    assert universe.members_on(date(2010, 1, 4)) == ["OLD"]
    assert universe.members_on(date(2015, 5, 29)) == ["OLD"]
    assert universe.members_on(date(2015, 6, 1)) == ["NEW"]
    assert universe.members_between(date(2008, 1, 1), date(2010, 1, 3)) == []
    assert universe.members_between(date(2015, 5, 31), date(2015, 6, 2)) == [
        "OLD",
        "NEW",
    ]
    assert universe.members_between(date(2015, 6, 1), date(2030, 1, 1)) == ["NEW"]


def test_a_name_that_left_and_came_back_is_a_member_in_both_spans(
    tmp_path: Path,
) -> None:
    universe = read_universe(
        _file(tmp_path, "AAA,2010-01-01,2012-01-01\nAAA,2014-01-01\nBBB,2010-01-01\n")
    )

    assert universe.tickers == ["AAA", "BBB"]
    assert universe.members_on(date(2013, 1, 1)) == ["BBB"]
    assert universe.members_on(date(2014, 1, 1)) == ["AAA", "BBB"]


def test_the_mask_keeps_a_row_only_on_the_dates_its_ticker_is_a_member(
    tmp_path: Path,
) -> None:
    universe = read_universe(
        _file(tmp_path, "OLD,2024-01-03,2024-01-05\nNEW,2024-01-04\n")
    )
    days = pd.bdate_range("2024-01-01", periods=7)
    index = pd.MultiIndex.from_product(
        [days, ["NEW", "OLD", "NONE"]], names=["date", "ticker"]
    )

    kept = index[universe.mask(index)]

    assert [(d.date().isoformat(), t) for d, t in kept] == [
        ("2024-01-03", "OLD"),
        ("2024-01-04", "NEW"),
        ("2024-01-04", "OLD"),
        ("2024-01-05", "NEW"),
        ("2024-01-08", "NEW"),
        ("2024-01-09", "NEW"),
    ]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("AAPL,2010-01-04\nSPY,2010-01-04\n", r"universe\.txt:2: SPY is the bench"),
        ("AAPL,2010-01-04,2012-01-01\naapl,2011-01-01\n", "overlapping"),
        ("AAPL,2010-01-04\nAAPL,2012-01-01\n", "overlapping"),
        ("# nothing\n", "no tickers"),
        ("AAPL\n", r"universe\.txt:1: expected `ticker,start\[,end\]`"),
        ("AAPL,,2012-01-01\n", "expected"),
        ("AAPL,2010-01-04,2012-01-01,x\n", "expected"),
        ("AAPL,Jan 4 2010\n", r"universe\.txt:1: dates are YYYY-MM-DD"),
        ("AAPL,2012-01-01,2012-01-01\n", "ends on 2012-01-01, not after"),
    ],
)
def test_a_malformed_file_is_an_error_that_names_the_line(
    tmp_path: Path, text: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        read_universe(_file(tmp_path, text))


def test_the_demo_universe_lists_thirty_members_on_every_date() -> None:
    demo = read_universe(UNIVERSES / "demo30.txt")

    assert len(demo.tickers) == 30
    assert demo.members_on(date(2000, 1, 3)) == demo.tickers
    assert demo.members_on(date(2040, 1, 1)) == demo.tickers


def test_the_sp500_universe_has_about_five_hundred_members_on_each_date() -> None:
    sp500 = read_universe(UNIVERSES / "sp500.txt")

    for year in range(2012, 2026):
        assert 495 <= len(sp500.members_on(date(year, 6, 30))) <= 515
    assert "AAPL" in sp500.members_on(date(2012, 6, 30))
    assert "TSLA" not in sp500.members_on(date(2019, 6, 30))
    assert "TSLA" in sp500.members_on(date(2021, 6, 30))
    assert len(sp500.tickers) > len(sp500.members_on(date(2026, 10, 9)))
