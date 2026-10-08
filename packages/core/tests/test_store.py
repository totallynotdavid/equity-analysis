from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from index_core.report import ModelInfo, ScoreRow, ScoresReport
from index_core.sources.base import PRICE_COLUMNS
from index_core.sources.synthetic import SyntheticFilings, SyntheticSource
from index_core.store import Store, StoreError, default_db_path


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
