from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from index_core.pipeline import run
from index_core.sources.base import SourceError
from index_core.sources.synthetic import SyntheticSource
from index_core.store import Store
from index_core.universe import read_universe


if TYPE_CHECKING:
    import pandas as pd


DEMO = Path(__file__).parents[3] / "universes" / "demo30.txt"
START, END = date(2024, 1, 1), date(2026, 9, 30)


def test_a_run_over_the_demo_universe_scores_thirty_stocks(tmp_path: Path) -> None:
    tickers = read_universe(DEMO)

    with Store.open(tmp_path / "db.sqlite") as store:
        report = run(SyntheticSource(), store, "demo", tickers, START, END)
        stored = store.latest_report()

    assert stored == report
    assert report.status == "experimental, not validated"
    assert report.as_of == date(2026, 9, 30)
    assert report.source == "synthetic"
    assert sorted(row.ticker for row in report.rows) == sorted(tickers)
    assert [row.rank for row in report.rows] == list(range(1, 31))
    assert all(1 <= row.score <= 10 for row in report.rows)
    assert {row.score for row in report.rows} == set(range(1, 11))
    probabilities = [row.prob for row in report.rows]
    assert probabilities == sorted(probabilities, reverse=True)
    assert len(set(probabilities)) == 30


def test_running_twice_gives_the_same_report(tmp_path: Path) -> None:
    tickers = read_universe(DEMO)[:12]

    with Store.open(tmp_path / "db.sqlite") as store:
        first = run(SyntheticSource(), store, "demo", tickers, START, END)
        second = run(SyntheticSource(), store, "demo", tickers, START, END)

    assert first == second


def test_a_ticker_without_enough_history_stops_the_run(tmp_path: Path) -> None:
    class LateListing(SyntheticSource):
        def fetch(self, ticker: str, start: date, end: date) -> pd.DataFrame:
            if ticker == "NEWCO":
                start = date(2026, 6, 1)
            return super().fetch(ticker, start, end)

    with (
        Store.open(tmp_path / "db.sqlite") as store,
        pytest.raises(ValueError, match=r"too little history .*NEWCO"),
    ):
        run(LateListing(), store, "demo", ["AAPL", "MSFT", "NEWCO"], START, END)


def test_a_source_failure_surfaces_and_stores_no_scores(tmp_path: Path) -> None:
    class Failing(SyntheticSource):
        def fetch(self, ticker: str, start: date, end: date) -> pd.DataFrame:
            raise SourceError("boom")

    with Store.open(tmp_path / "db.sqlite") as store:
        with pytest.raises(SourceError, match="boom"):
            run(Failing(), store, "demo", ["AAPL"], START, END)
        assert store.latest_report() is None
