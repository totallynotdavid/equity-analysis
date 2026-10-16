from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from index_core.pipeline import (
    coverage,
    ingest,
    ingest_filings,
    missing_filings,
    missing_prices,
    run,
)
from index_core.report import LeftOut
from index_core.sources.base import NotFoundError, Security, SourceError
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


def test_a_company_the_filings_source_does_not_know_is_left_out(
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
    assert sorted(row.ticker for row in report.rows) == sorted(set(tickers) - {"MSFT"})
    assert report.left_out == [LeftOut(ticker="MSFT", reason="no filings")]


def test_a_run_where_no_ticker_has_filings_fails_instead_of_scoring_nothing(
    tmp_path: Path,
) -> None:
    class NoFilings(SyntheticFilings):
        def facts(self, ticker: str, end: date) -> pd.DataFrame:
            raise NotFoundError(f"no company for ticker {ticker}")

    with Store.open(tmp_path / "db.sqlite") as store:
        with pytest.raises(ValueError, match="no ticker has both prices and filings"):
            run(SyntheticSource(), NoFilings(), store, always(["AAPL"]), START, END)
        assert store.latest_report() is None


def test_a_ticker_the_price_source_does_not_know_is_scored_without_it(
    tmp_path: Path,
) -> None:
    class Unlisted(SyntheticSource):
        def fetch(self, ticker: str, start: date, end: date) -> pd.DataFrame:
            if ticker == "MSFT":
                raise NotFoundError("no ticker MSFT")
            return super().fetch(ticker, start, end)

    tickers = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "JPM"]

    with Store.open(tmp_path / "db.sqlite") as store:
        report = run(
            Unlisted(), SyntheticFilings(), store, always(tickers, "demo"), START, END
        )
        missing = missing_prices(store, tickers)

    assert missing == ["MSFT"]
    assert sorted(row.ticker for row in report.rows) == sorted(set(tickers) - {"MSFT"})
    assert report.left_out == [LeftOut(ticker="MSFT", reason="no prices")]


def test_a_benchmark_the_price_source_does_not_know_stops_the_run(
    tmp_path: Path,
) -> None:
    class NoBenchmark(SyntheticSource):
        def fetch(self, ticker: str, start: date, end: date) -> pd.DataFrame:
            if ticker == "SPY":
                raise NotFoundError("no ticker SPY")
            return super().fetch(ticker, start, end)

    with Store.open(tmp_path / "db.sqlite") as store:
        with pytest.raises(NotFoundError, match="SPY"):
            run(NoBenchmark(), SyntheticFilings(), store, always(["AAPL"]), START, END)
        assert store.latest_report() is None


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


def test_ingesting_filings_stores_who_each_company_is_and_its_sector(
    tmp_path: Path,
) -> None:
    class Delisted(SyntheticFilings):
        def security(self, ticker: str) -> Security:
            if ticker == "MSFT":
                raise NotFoundError("no company for ticker MSFT")
            return super().security(ticker)

    tickers = ["AAPL", "MSFT"]
    with Store.open(tmp_path / "db.sqlite") as store:
        ingest_filings(SyntheticFilings(), store, tickers, END, {"AAPL": "Tech"})
        both = store.read_security(tickers)
        ingest_filings(Delisted(), store, tickers, END)
        left = store.read_security(tickers)

    assert both["name"].tolist() == ["AAPL Holdings Inc.", "MSFT Holdings Inc."]
    assert both["sector"].iloc[0] == "Tech"
    assert pd.isna(both["sector"].iloc[1])
    assert left["ticker"].tolist() == ["AAPL"]
    assert left["sector"].isna().all()


def test_a_run_reports_each_scored_ticker_with_its_percentiles(
    tmp_path: Path,
) -> None:
    tickers = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "JPM"]
    sectors = dict.fromkeys(tickers[:6], "Tech") | {"TSLA": "Auto", "JPM": "Banks"}
    universe = Universe("demo", always(tickers).memberships, sectors)

    with Store.open(tmp_path / "db.sqlite") as store:
        run(SyntheticSource(), SyntheticFilings(), store, universe, START, END)
        reports = store.ticker_reports("demo")
        daily = store.read_metrics("demo", "AAPL")

    assert [r.ticker for r in reports] == sorted(tickers)
    aapl = next(r for r in reports if r.ticker == "AAPL")
    assert (aapl.as_of, aapl.sector, aapl.name) == (END, "Tech", "AAPL Holdings Inc.")
    assert aapl.facts_source == "synthetic"
    series = {s.metric: s for s in aapl.series}
    assert series["revenue"].pct_universe is not None
    assert series["revenue"].pct_sector is not None
    jpm = next(r for r in reports if r.ticker == "JPM")
    assert {s.metric: s for s in jpm.series}["revenue"].pct_sector is None
    # The page shows the figure that was ranked.
    last = daily[daily["as_of"] == pd.Timestamp(END)].set_index("metric")
    for name, shown in series.items():
        assert shown.points[-1].end <= END
        if name in ("revenue", "net_income", "total_assets"):
            assert shown.points[-1].value == pytest.approx(last.loc[name, "value"])


def test_a_company_with_no_facts_has_no_report(tmp_path: Path) -> None:
    class Unlisted(SyntheticFilings):
        def facts(self, ticker: str, end: date) -> pd.DataFrame:
            if ticker == "MSFT":
                raise NotFoundError("no company for ticker MSFT")
            return super().facts(ticker, end)

    tickers = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "JPM"]
    with Store.open(tmp_path / "db.sqlite") as store:
        run(SyntheticSource(), Unlisted(), store, always(tickers, "demo"), START, END)
        reports = store.ticker_reports("demo")

    assert "MSFT" not in {r.ticker for r in reports}
    assert len(reports) == 7


def test_the_coverage_of_a_universe_names_what_a_failing_name_lacks(
    tmp_path: Path,
) -> None:
    class NoTax(SyntheticFilings):
        def facts(self, ticker: str, end: date) -> pd.DataFrame:
            facts = super().facts(ticker, end)
            return facts[facts["concept"] != "income_tax"]

    with Store.open(tmp_path / "db.sqlite") as store:
        ingest_filings(NoTax(), store, ["AAPL"], END)
        result = coverage(store, always(["AAPL"]), END)

    assert result.as_of == END
    assert not result.present.loc["AAPL", "income_tax"]
    assert result.present.loc["AAPL", "revenue"]
    assert result.no_company == []
    dead = result.tags[result.tags["fresh"] == 0]
    assert "IncomeTaxExpenseBenefit" in set(dead["tag"])
