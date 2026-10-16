import sqlite3

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from index_core.report import (
    Filing,
    LeftOut,
    ModelInfo,
    Part,
    Point,
    ScoreRow,
    ScoresReport,
    Series,
    TickerReport,
)
from index_core.sources.base import PRICE_COLUMNS, Security
from index_core.sources.synthetic import (
    SyntheticFactors,
    SyntheticFilings,
    SyntheticSource,
)
from index_core.store import SCHEMA_VERSION, Store, StoreError, default_db_path


def _bars() -> pd.DataFrame:
    return SyntheticSource().fetch("AAA", date(2024, 1, 1), date(2024, 1, 31))


def _record_sources(store: Store) -> None:
    store.upsert_prices("tiingo", "AAA", _bars())
    store.replace_facts(
        "synthetic", "AAA", SyntheticFilings().facts("AAA", date(2024, 12, 31))
    )


def _report(as_of: date, tickers: list[str], universe: str = "demo") -> ScoresReport:
    return ScoresReport(
        as_of=as_of,
        universe=universe,
        price_source="tiingo",
        facts_source="synthetic",
        horizon_days=63,
        model=ModelInfo(train_rows=10, holdout_rows=4, holdout_auc=None),
        rows=[
            ScoreRow(ticker=t, rank=i, score=10 - i, prob=0.5 + i / 100)
            for i, t in enumerate(tickers, start=1)
        ],
    )


def test_prices_round_trip(tmp_path: Path) -> None:
    bars = _bars()
    with Store.open(tmp_path / "db.sqlite") as store:
        store.upsert_prices("synthetic", "AAA", bars)
        stored = store.read_prices(["AAA"])

    assert list(stored["ticker"].unique()) == ["AAA"]
    pd.testing.assert_frame_equal(
        stored.set_index("date")[list(PRICE_COLUMNS)],
        bars,
        check_freq=False,
        check_names=False,
    )


def test_fetching_a_range_again_replaces_the_stored_bars(tmp_path: Path) -> None:
    bars = _bars()
    revised = bars * 2
    with Store.open(tmp_path / "db.sqlite") as store:
        store.upsert_prices("synthetic", "AAA", bars)
        store.upsert_prices("synthetic", "AAA", revised)
        stored = store.read_prices(["AAA"])

    assert len(stored) == len(bars)
    assert stored["adj_close"].to_list() == pytest.approx(
        revised["adj_close"].to_list()
    )


def test_one_database_refuses_prices_from_two_sources(tmp_path: Path) -> None:
    with Store.open(tmp_path / "db.sqlite") as store:
        store.upsert_prices("synthetic", "AAA", _bars())
        with pytest.raises(StoreError, match="holds synthetic prices"):
            store.upsert_prices("tiingo", "AAA", _bars())


def test_facts_round_trip_with_missing_starts_kept(tmp_path: Path) -> None:
    facts = SyntheticFilings().facts("AAA", date(2024, 12, 31))
    assert facts["start"].isna().any(), "balance facts have no start"
    assert facts["start"].notna().any()

    with Store.open(tmp_path / "db.sqlite") as store:
        store.replace_facts("synthetic", "AAA", facts)
        stored = store.read_facts(["AAA"])

    assert set(stored["ticker"]) == {"AAA"}
    key = ["concept", "end", "start", "filed"]
    pd.testing.assert_frame_equal(
        stored.drop(columns="ticker").sort_values(key).reset_index(drop=True),
        facts.sort_values(key).reset_index(drop=True),
    )


def test_replacing_facts_drops_what_the_ticker_had_before(tmp_path: Path) -> None:
    filings = SyntheticFilings()
    with Store.open(tmp_path / "db.sqlite") as store:
        store.replace_facts(
            "synthetic", "AAA", filings.facts("AAA", date(2024, 12, 31))
        )
        store.replace_facts(
            "synthetic", "BBB", filings.facts("BBB", date(2024, 12, 31))
        )
        earlier = filings.facts("AAA", date(2022, 12, 31))
        store.replace_facts("synthetic", "AAA", earlier)
        stored = store.read_facts(["AAA", "BBB"])

    assert len(stored[stored["ticker"] == "AAA"]) == len(earlier)
    assert not stored[stored["ticker"] == "BBB"].empty


def test_storing_no_facts_does_not_lock_the_database_to_a_source(
    tmp_path: Path,
) -> None:
    facts = SyntheticFilings().facts("AAA", date(2024, 12, 31))
    with Store.open(tmp_path / "db.sqlite") as store:
        store.replace_facts("synthetic", "AAA", facts.iloc[0:0])
        assert store.facts_source() is None

        store.replace_facts("edgar", "AAA", facts)

        assert store.facts_source() == "edgar"


def test_one_database_refuses_facts_from_two_sources(tmp_path: Path) -> None:
    facts = SyntheticFilings().facts("AAA", date(2024, 12, 31))
    with Store.open(tmp_path / "db.sqlite") as store:
        assert store.facts_source() is None
        store.replace_facts("synthetic", "AAA", facts)
        with pytest.raises(StoreError, match="holds synthetic facts"):
            store.replace_facts("edgar", "AAA", facts)
        assert store.facts_source() == "synthetic"


def test_sources_are_checked_without_writing_anything(tmp_path: Path) -> None:
    facts = SyntheticFilings().facts("AAA", date(2024, 12, 31))
    with Store.open(tmp_path / "db.sqlite") as store:
        store.require_sources("tiingo", "edgar")
        store.upsert_prices("synthetic", "AAA", _bars())
        store.replace_facts("synthetic", "AAA", facts)

        store.require_sources("synthetic", "synthetic")
        with pytest.raises(StoreError, match="holds synthetic prices"):
            store.require_sources("tiingo", "synthetic")
        with pytest.raises(StoreError, match="holds synthetic facts"):
            store.require_sources("synthetic", "edgar")


def test_a_database_from_before_the_current_layout_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite"
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE prices (ticker TEXT, date TEXT, close REAL, "
        "PRIMARY KEY (ticker, date))"
    )
    old.close()

    with pytest.raises(StoreError, match="older layout; delete it"):
        Store.open(path)
    with pytest.raises(StoreError, match="older layout; delete it"):
        Store.open(path, read_only=True)
    Store.open(tmp_path / "new.sqlite").close()
    Store.open(tmp_path / "new.sqlite", read_only=True).close()
    Store.open(tmp_path / "new.sqlite").close()


def test_the_default_database_follows_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("INDEX_DB", raising=False)
    assert default_db_path() == Path("data/index.sqlite")

    monkeypatch.setenv("INDEX_DB", str(tmp_path / "mine.sqlite"))
    assert default_db_path() == tmp_path / "mine.sqlite"


def test_the_latest_report_is_returned_with_rows_in_rank_order(tmp_path: Path) -> None:
    with Store.open(tmp_path / "db.sqlite") as store:
        _record_sources(store)
        assert store.latest_report() is None
        store.save_report(_report(date(2024, 1, 30), ["OLD"]))
        store.save_report(_report(date(2024, 1, 31), ["B", "A"]))
        latest = store.latest_report()

    assert latest == _report(date(2024, 1, 31), ["B", "A"])


def test_a_report_keeps_its_own_sources_and_left_out_tickers(tmp_path: Path) -> None:
    report = _report(date(2024, 1, 31), ["A"]).model_copy(
        update={
            "facts_source": "edgar",
            "left_out": [
                LeftOut(ticker="X", reason="no filings"),
                LeftOut(ticker="Y", reason="no prices"),
            ],
        }
    )
    with Store.open(tmp_path / "db.sqlite") as store:
        store.save_report(report)
        latest = store.latest_report()
        # The database holds no prices or filings, so it claims no source for them.
        assert (store.price_source(), store.facts_source()) == (None, None)

    assert latest == report


def test_saving_a_report_again_replaces_that_date(tmp_path: Path) -> None:
    with Store.open(tmp_path / "db.sqlite") as store:
        _record_sources(store)
        store.save_report(_report(date(2024, 1, 31), ["A", "B"]))
        store.save_report(_report(date(2024, 1, 31), ["C"]))
        latest = store.latest_report()

    assert latest is not None
    assert [row.ticker for row in latest.rows] == ["C"]


def test_a_run_over_another_universe_on_the_same_day_keeps_both(
    tmp_path: Path,
) -> None:
    day = date(2024, 1, 31)
    with Store.open(tmp_path / "db.sqlite") as store:
        _record_sources(store)
        store.save_report(_report(day, ["A", "B"], universe="demo"))
        store.save_report(_report(day, ["C"], universe="other"))

        assert store.latest_report("demo") == _report(day, ["A", "B"], "demo")
        assert store.latest_report("other") == _report(day, ["C"], "other")
        assert store.latest_report() == _report(day, ["C"], "other")
        assert store.latest_report("missing") is None


def test_a_read_only_store_cannot_write(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite"
    Store.open(path).close()

    with (
        Store.open(path, read_only=True) as store,
        pytest.raises(Exception, match="readonly"),
    ):
        store.save_report(_report(date(2024, 1, 31), ["A"]))


def test_factors_round_trip_and_a_second_fetch_replaces_them(tmp_path: Path) -> None:
    factors = SyntheticFactors().fetch().iloc[:50]
    with Store.open(tmp_path / "db.sqlite") as store:
        assert store.factors_source() is None
        assert store.read_factors().empty

        store.replace_factors("synthetic", factors)
        pd.testing.assert_frame_equal(store.read_factors(), factors, check_freq=False)
        store.replace_factors("synthetic", factors.iloc[:10])

        assert len(store.read_factors()) == 10
        assert store.factors_source() == "synthetic"


def test_one_database_refuses_factors_from_two_sources(tmp_path: Path) -> None:
    factors = SyntheticFactors().fetch().iloc[:10]
    with Store.open(tmp_path / "db.sqlite") as store:
        store.replace_factors("synthetic", factors)
        with pytest.raises(StoreError, match="holds synthetic factors"):
            store.replace_factors("french", factors)


def test_the_ledger_counts_a_specification_once_and_keeps_its_latest_sharpe(
    tmp_path: Path,
) -> None:
    day = date(2024, 6, 28)
    with Store.open(tmp_path / "db.sqlite") as store:
        assert store.trial_sharpes() == []

        store.record_trial("a", "first", day, 24, 0.10)
        store.record_trial("b", "second", day, 24, -0.05)
        store.record_trial("a", "first again", day, 25, 0.12)

        assert sorted(store.trial_sharpes()) == [-0.05, 0.12]


def test_a_database_of_the_previous_layout_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite"
    Store.open(path).close()
    previous = sqlite3.connect(path)
    previous.execute(f"PRAGMA user_version = {SCHEMA_VERSION - 1}")
    previous.close()

    with pytest.raises(StoreError, match="older layout; delete it"):
        Store.open(path)


def _ticker_report(ticker: str, as_of: date, universe: str = "demo") -> TickerReport:
    filing = Filing(
        accn="0000000001-24-000001",
        form="10-K",
        fy=2023,
        fp="FY",
        filed=date(2024, 2, 1),
        url="https://www.sec.gov/Archives/edgar/data/1/000000000124000001/"
        "0000000001-24-000001-index.htm",
    )
    part = Part(
        metric="revenue",
        sign=1,
        tag="Revenues",
        start=date(2023, 1, 1),
        end=date(2023, 12, 31),
        value=100.0,
        filing=filing,
    )
    return TickerReport(
        ticker=ticker,
        universe=universe,
        as_of=as_of,
        facts_source="synthetic",
        name=f"{ticker} Inc.",
        cik=1,
        sic="7372",
        sic_description="Services-Prepackaged Software",
        sector=None,
        series=[
            Series(
                metric="revenue",
                group="Income statement",
                label="Revenue",
                unit="USD",
                pct_universe=None,
                pct_sector=None,
                points=[Point(end=date(2023, 12, 31), value=100.0, parts=[part])],
            )
        ],
    )


def test_a_security_round_trips_and_is_replaced_by_the_next(tmp_path: Path) -> None:
    with Store.open(tmp_path / "db.sqlite") as store:
        store.replace_security(
            "AAA", Security(1, "Aaa Inc.", "3490", "Metal"), "Energy"
        )
        store.replace_security("BBB", Security(2, "Bbb Inc.", "7372", "Software"), None)
        store.replace_security("AAA", Security(1, "Aaa Corp.", "3490", "Metal"), None)
        store.replace_security("CCC", None, None)
        stored = store.read_security(["AAA", "BBB", "CCC"])

    assert stored["ticker"].tolist() == ["AAA", "BBB"]
    assert stored["name"].tolist() == ["Aaa Corp.", "Bbb Inc."]
    assert stored["sector"].isna().all()


def test_metrics_round_trip_by_ticker_and_replace_those_of_the_universe(
    tmp_path: Path,
) -> None:
    def frame(value: float) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "ticker": ["AAA", "AAA", "BBB"],
                "as_of": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-03"]),
                "metric": ["revenue", "revenue", "revenue"],
                "value": [value, value + 1, value + 2],
                "pct_universe": [None, 50.0, 100.0],
                "pct_sector": [None, None, None],
            }
        )

    with Store.open(tmp_path / "db.sqlite") as store:
        store.replace_metrics("demo", frame(1.0))
        store.replace_metrics("other", frame(10.0))
        store.replace_metrics("demo", frame(5.0))
        stored = store.read_metrics("demo", "AAA")
        other = store.read_metrics("other", "BBB")

    assert stored["value"].tolist() == [5.0, 6.0]
    assert stored["as_of"].tolist() == list(
        pd.to_datetime(["2024-01-02", "2024-01-03"])
    )
    assert stored["pct_universe"].isna().tolist() == [True, False]
    assert other["value"].tolist() == [12.0]


def test_the_latest_report_of_a_ticker_is_returned_whatever_the_universe(
    tmp_path: Path,
) -> None:
    older = _ticker_report("AAA", date(2024, 1, 2))
    newer = _ticker_report("AAA", date(2024, 1, 3), universe="other")

    with Store.open(tmp_path / "db.sqlite") as store:
        store.replace_ticker_reports(
            "demo", [older, _ticker_report("BBB", date(2024, 1, 2))]
        )
        store.add_ticker_reports([newer])

        assert store.ticker_report("AAA") == newer
        assert store.ticker_report("NONE") is None
        assert [r.ticker for r in store.ticker_reports("demo")] == ["AAA", "BBB"]

        store.replace_ticker_reports("demo", [])

        assert store.ticker_reports("demo") == []
        assert store.ticker_report("AAA") == newer
