from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from index_core.pipeline import (
    coverage,
    ingest,
    ingest_filings,
    missing_filings,
    run,
)
from index_core.sources.base import NotFoundError, SourceError
from index_core.sources.synthetic import SyntheticFilings, SyntheticSource
from index_core.store import Store
from index_core.universe import Membership, Universe, read_universe
from membership import always


DEMO = Path(__file__).parents[3] / "universes" / "demo30.txt"
START, END = date(2024, 1, 1), date(2026, 9, 30)


def test_a_run_over_the_demo_universe_scores_thirty_stocks(tmp_path: Path) -> None:
    universe = read_universe(DEMO)

    with Store.open(tmp_path / "db.sqlite") as store:
        report = run(SyntheticSource(), SyntheticFilings(), store, universe, START, END)
        stored = store.latest_report()

    assert stored == report
    assert report.universe == "demo30"
    assert report.status == "experimental, not validated"
    assert report.as_of == date(2026, 9, 30)
    assert (report.price_source, report.facts_source) == ("synthetic", "synthetic")
    assert sorted(row.ticker for row in report.rows) == sorted(universe.tickers)
    assert [row.rank for row in report.rows] == list(range(1, 31))
    assert all(1 <= row.score <= 10 for row in report.rows)
    assert {row.score for row in report.rows} == set(range(1, 11))
    probabilities = [row.prob for row in report.rows]
    assert probabilities == sorted(probabilities, reverse=True)
    assert len(set(probabilities)) == 30


def test_running_twice_gives_the_same_report(tmp_path: Path) -> None:
    universe = always(read_universe(DEMO).tickers[:12], "demo")

    prices, filings = SyntheticSource(), SyntheticFilings()

    with Store.open(tmp_path / "db.sqlite") as store:
        first = run(prices, filings, store, universe, START, END)
        second = run(prices, filings, store, universe, START, END)

    assert first == second


def test_a_run_stores_the_filings_it_scored_with(tmp_path: Path) -> None:
    tickers = read_universe(DEMO).tickers[:12]
    universe = always(tickers, "demo")

    with Store.open(tmp_path / "db.sqlite") as store:
        run(SyntheticSource(), SyntheticFilings(), store, universe, START, END)
        facts = store.read_facts(tickers)
        source = store.facts_source()
        missing = missing_filings(store, tickers)
        concepts = coverage(store, universe)

    assert source == "synthetic"
    assert set(facts["ticker"]) == set(tickers)
    assert facts["filed"].max() <= pd.Timestamp(END)
    assert missing == []
    assert concepts.as_of == END
    assert concepts.facts_source == "synthetic"
    assert concepts.concepts.index.tolist() == tickers
    assert (concepts.concepts > 0).all()


def test_a_run_scores_and_fetches_only_the_names_that_are_members(
    tmp_path: Path,
) -> None:
    universe = Universe(
        "mixed",
        (
            Membership("AAPL", date(2020, 1, 1), None),
            Membership("MSFT", date(2020, 1, 1), None),
            Membership("NVDA", date(2020, 1, 1), None),
            Membership("AMZN", date(2020, 1, 1), None),
            Membership("GOOGL", date(2020, 1, 1), None),
            Membership("META", date(2020, 1, 1), None),
            Membership("JPM", date(2020, 1, 1), None),
            Membership("GONE", date(2020, 1, 1), date(2024, 6, 3)),
            Membership("LONG_GONE", date(2010, 1, 1), date(2015, 1, 1)),
        ),
    )

    with Store.open(tmp_path / "db.sqlite") as store:
        report = run(SyntheticSource(), SyntheticFilings(), store, universe, START, END)
        fetched = set(store.read_prices(universe.tickers)["ticker"])

    assert sorted(row.ticker for row in report.rows) == sorted(universe.members_on(END))
    assert "GONE" in fetched
    assert "LONG_GONE" not in fetched


def test_a_company_the_filings_source_does_not_know_keeps_its_prices_only(
    tmp_path: Path,
) -> None:
    class Unlisted(SyntheticFilings):
        def facts(self, ticker: str, end: date) -> pd.DataFrame:
            if ticker == "MSFT":
                raise NotFoundError("no company for ticker MSFT")
            return super().facts(ticker, end)

    tickers = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "JPM"]
    universe = always(tickers, "demo")

    with Store.open(tmp_path / "db.sqlite") as store:
        report = run(SyntheticSource(), Unlisted(), store, universe, START, END)
        missing = missing_filings(store, tickers)
        counts = coverage(store, universe).concepts

    assert missing == ["MSFT"]
    assert counts["MSFT"] == 0
    assert sorted(row.ticker for row in report.rows) == sorted(tickers)


def test_a_company_that_loses_its_filings_does_not_keep_stale_facts(
    tmp_path: Path,
) -> None:
    class Delisted(SyntheticFilings):
        def facts(self, ticker: str, end: date) -> pd.DataFrame:
            if ticker == "MSFT":
                raise NotFoundError("no company for ticker MSFT")
            return super().facts(ticker, end)

    tickers = ["AAPL", "MSFT"]
    with Store.open(tmp_path / "db.sqlite") as store:
        ingest(SyntheticSource(), store, tickers, START, END)
        ingest_filings(SyntheticFilings(), store, tickers, END)
        assert missing_filings(store, tickers) == []

        ingest_filings(Delisted(), store, tickers, END)
        missing = missing_filings(store, tickers)
        facts = store.read_facts(tickers)
        counts = coverage(store, always(tickers)).concepts

    assert missing == ["MSFT"]
    assert set(facts["ticker"]) == {"AAPL"}
    assert counts["MSFT"] == 0
    assert counts["AAPL"] > 0


def test_any_other_filings_failure_stops_the_run_before_scoring(
    tmp_path: Path,
) -> None:
    class Failing(SyntheticFilings):
        def facts(self, ticker: str, end: date) -> pd.DataFrame:
            raise SourceError("boom")

    with Store.open(tmp_path / "db.sqlite") as store:
        with pytest.raises(SourceError, match="boom"):
            run(SyntheticSource(), Failing(), store, always(["AAPL"]), START, END)
        assert store.latest_report() is None


def test_ingesting_filings_again_replaces_what_was_stored(tmp_path: Path) -> None:
    with Store.open(tmp_path / "db.sqlite") as store:
        ingest_filings(SyntheticFilings(), store, ["AAPL"], date(2025, 12, 31))
        first = store.read_facts(["AAPL"])
        ingest_filings(SyntheticFilings(), store, ["AAPL"], END)
        second = store.read_facts(["AAPL"])

    assert len(second) > len(first)
    assert second["filed"].max() > first["filed"].max()


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
        run(
            LateListing(),
            SyntheticFilings(),
            store,
            always(["AAPL", "MSFT", "NEWCO"], "demo"),
            START,
            END,
        )


def test_a_source_failure_surfaces_and_stores_no_scores(tmp_path: Path) -> None:
    class Failing(SyntheticSource):
        def fetch(self, ticker: str, start: date, end: date) -> pd.DataFrame:
            raise SourceError("boom")

    with Store.open(tmp_path / "db.sqlite") as store:
        with pytest.raises(SourceError, match="boom"):
            run(Failing(), SyntheticFilings(), store, always(["AAPL"]), START, END)
        assert store.latest_report() is None
