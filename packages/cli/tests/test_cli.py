import json
import socket

from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from index_cli import main as cli
from index_cli.main import main
from index_core.pipeline import ingest
from index_core.sources.base import NotFoundError
from index_core.sources.edgar import CONCEPTS
from index_core.sources.synthetic import SyntheticSource
from index_core.store import Store


def _without_openmp(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(name: str) -> None:
        raise OSError(f"{name}: cannot open shared object file")

    monkeypatch.setattr(
        "index_core.model.importlib", SimpleNamespace(import_module=refuse)
    )
    monkeypatch.setattr("index_core.model.sys", SimpleNamespace(platform="linux"))


DEMO = Path(__file__).parents[3] / "universes" / "demo30.txt"
END = date(2026, 9, 30)
WINDOW = ["--start", "2024-01-01", "--end", END.isoformat()]
OFFLINE = ["--prices", "synthetic", "--filings", "synthetic"]
MEMBERS = [f"M{i:02d}" for i in range(12)]


def _members(directory: Path) -> Path:
    """Write the small universe used by tests that do not need `DEMO`."""
    path = directory / "members.txt"
    path.write_text("".join(f"{ticker},2000-01-03\n" for ticker in MEMBERS))
    return path


def _run(tmp_path: Path) -> Path:
    out = tmp_path / "scores.json"
    main(
        [
            *["run", "--universe", str(_members(tmp_path)), *OFFLINE, *WINDOW],
            *["--db", str(tmp_path / "db.sqlite"), "--out", str(out)],
        ]
    )
    return out


def test_help_prints_usage_and_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.startswith("usage: eq")


def test_unknown_argument_exits_nonzero() -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--no-such-flag"])

    assert exit_info.value.code != 0


def test_run_writes_thirty_scored_rows_with_an_as_of_date(ran: Path) -> None:
    scores = json.loads(ran.read_text())

    assert scores["as_of"] == "2026-09-30"
    assert scores["status"] == "experimental, not validated"
    assert (scores["price_source"], scores["facts_source"]) == (
        "synthetic",
        "synthetic",
    )
    rows = scores["rows"]
    assert len(rows) == 30
    assert [row["rank"] for row in rows] == list(range(1, 31))
    assert all(
        isinstance(row["score"], int) and 1 <= row["score"] <= 10 for row in rows
    )
    assert len({row["ticker"] for row in rows}) == 30


def test_export_rewrites_the_stored_scores_unchanged(ran: Path) -> None:
    exported = ran.parent / "exported.json"

    main(["export", "--db", str(ran.parent / "db.sqlite"), "--out", str(exported)])

    assert exported.read_text() == ran.read_text()


def test_import_stores_a_scores_file_so_export_returns_it_unchanged(
    ran: Path,
) -> None:
    tmp_path = ran.parent
    scores = json.loads(ran.read_text())
    scores["price_source"], scores["facts_source"] = "tiingo", "edgar"
    published = tmp_path / "published.json"
    published.write_text(json.dumps(scores, indent=2) + "\n")
    database = str(tmp_path / "published.sqlite")

    main(["import", "--scores", str(published), "--db", database])
    exported = tmp_path / "exported.json"
    main(["export", "--db", database, "--out", str(exported)])

    assert exported.read_text() == published.read_text()


def test_export_keeps_the_order_of_the_imported_rows_and_left_out_names(
    ran: Path,
) -> None:
    tmp_path = ran.parent
    scores = json.loads(ran.read_text())
    scores["rows"].reverse()
    scores["left_out"] = [
        {"ticker": "ZZZ", "reason": "no prices"},
        {"ticker": "AAA", "reason": "no filings"},
    ]
    published = tmp_path / "published.json"
    published.write_text(json.dumps(scores, indent=2) + "\n")
    database = str(tmp_path / "published.sqlite")

    main(["import", "--scores", str(published), "--db", database])
    exported = tmp_path / "exported.json"
    main(["export", "--db", database, "--out", str(exported)])

    assert exported.read_text() == published.read_text()


def test_import_rejects_a_file_that_is_not_a_scores_report(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text('{"as_of": "2026-09-30"}')

    with pytest.raises(SystemExit, match=r"^eq: .*validation error"):
        main(["import", "--scores", str(broken), "--db", str(tmp_path / "db.sqlite")])


def test_export_without_a_database_fails_with_a_message(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match=r"does not exist; run `eq run` first"):
        main(["export", "--db", str(tmp_path / "none.sqlite")])


def test_a_missing_database_points_to_the_one_beside_it(ran: Path) -> None:
    with pytest.raises(SystemExit, match=r"found db\.sqlite beside it.*--db"):
        main(["export", "--db", str(ran.parent / "index.sqlite")])


def test_tiingo_run_without_a_key_fails_before_fetching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)

    with pytest.raises(SystemExit, match="TIINGO_API_KEY is not set"):
        main(["run", "--universe", str(DEMO), "--db", str(tmp_path / "db.sqlite")])


def test_tiingo_run_without_a_contact_fails_before_fetching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TIINGO_API_KEY", "key")
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)

    with pytest.raises(SystemExit, match="SEC_USER_AGENT is not set"):
        main(["run", "--universe", str(DEMO), "--db", str(tmp_path / "db.sqlite")])


def test_a_run_on_a_database_of_other_sources_fails_before_fetching(
    ran: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TIINGO_API_KEY", "key")
    monkeypatch.setenv("SEC_USER_AGENT", "Mozilla/5.0 Chrome/155.0.0.0 Safari/537.36")
    monkeypatch.setattr(
        "index_core.sources.tiingo.BASE_URL", "http://127.0.0.1:1", raising=True
    )

    with pytest.raises(SystemExit, match="holds synthetic prices"):
        main(["run", "--universe", str(DEMO), "--db", str(ran.parent / "db.sqlite")])


def test_prices_and_filings_are_chosen_independently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)

    with pytest.raises(SystemExit, match="SEC_USER_AGENT is not set"):
        main(
            [
                *["run", "--universe", str(DEMO), "--prices", "synthetic"],
                *["--db", str(tmp_path / "db.sqlite")],
            ]
        )


def test_run_and_export_share_the_default_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("INDEX_DB", raising=False)

    universe = _members(tmp_path)

    main(["run", "--universe", str(universe), *OFFLINE, *WINDOW])
    main(["export", "--out", str(tmp_path / "exported.json")])
    main(["coverage", "--universe", str(universe)])
    with pytest.raises(SystemExit, match=r"days of SPY prices are stored"):
        main(["backtest", "--universe", str(universe)])

    assert sorted(path.name for path in (tmp_path / "data").glob("*.sqlite")) == [
        "index.sqlite"
    ]
    assert (tmp_path / "exported.json").exists()


def test_mixed_sources_are_recorded_in_the_cli_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class TiingoForTest(SyntheticSource):
        name = "tiingo"

    monkeypatch.setitem(cli.PRICES, "tiingo", lambda _: TiingoForTest())
    out = tmp_path / "scores.json"

    main(
        [
            *["run", "--universe", str(_members(tmp_path)), "--prices", "tiingo"],
            *["--filings", "synthetic", *WINDOW],
            *["--db", str(tmp_path / "db.sqlite"), "--out", str(out)],
        ]
    )

    scores = json.loads(out.read_text())
    assert (scores["price_source"], scores["facts_source"]) == (
        "tiingo",
        "synthetic",
    )


class _NoMicrosoft(SyntheticSource):
    def fetch(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        if ticker == "MSFT":
            raise NotFoundError("no ticker MSFT")
        return super().fetch(ticker, start, end)


def test_a_run_names_the_tickers_the_price_source_lacks_and_hands_over_the_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caches: list[Path] = []

    def source(cache: Path) -> SyntheticSource:
        caches.append(cache)
        return _NoMicrosoft()

    monkeypatch.setitem(cli.PRICES, "synthetic", source)
    universe = tmp_path / "members.txt"
    universe.write_text("".join(f"{t},2000-01-03\n" for t in [*MEMBERS, "MSFT"]))
    cache = tmp_path / "responses"

    main(
        [
            *["run", "--universe", str(universe), *OFFLINE, *WINDOW],
            *["--db", str(tmp_path / "db.sqlite"), "--cache", str(cache)],
            *["--out", str(tmp_path / "scores.json")],
        ]
    )

    assert caches == [cache]
    assert "eq: no prices for 1 tickers (MSFT); they are left out" in (
        capsys.readouterr().err
    )
    scores = json.loads((tmp_path / "scores.json").read_text())
    assert "MSFT" not in {row["ticker"] for row in scores["rows"]}


def test_a_missing_openmp_runtime_stops_a_run_before_it_fetches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "db.sqlite"
    _without_openmp(monkeypatch)

    with pytest.raises(SystemExit, match=r"eq: .*apt-get install libgomp1"):
        main(["run", "--universe", str(DEMO), *OFFLINE, "--db", str(database)])

    with Store.open(database, read_only=True) as store:
        assert store.price_source() is None


def test_export_and_coverage_work_without_the_openmp_runtime(
    ran: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tmp_path = ran.parent
    capsys.readouterr()
    _without_openmp(monkeypatch)
    database = str(tmp_path / "db.sqlite")

    main(["export", "--db", database, "--out", str(tmp_path / "again.json")])
    main(["coverage", "--universe", str(DEMO), "--db", database])

    assert (tmp_path / "again.json").exists()
    assert "Fundamentals coverage" in capsys.readouterr().out


def test_run_writes_a_report_per_ticker_beside_the_scores(ran: Path) -> None:
    tmp_path = ran.parent

    files = sorted((tmp_path / "tickers").glob("*.json"))
    assert len(files) == 30
    report = json.loads((tmp_path / "tickers" / "AAPL.json").read_text())
    assert (report["ticker"], report["universe"], report["as_of"]) == (
        "AAPL",
        "demo30",
        "2026-09-30",
    )
    assert report["name"] == "AAPL Holdings Inc."
    revenue = next(s for s in report["series"] if s["metric"] == "revenue")
    point = revenue["points"][-1]
    assert point["parts"][0]["filing"]["url"].startswith(
        f"https://www.sec.gov/Archives/edgar/data/{report['cik']}/"
    )


def test_a_run_replaces_the_reports_of_an_earlier_run(tmp_path: Path) -> None:
    stale = tmp_path / "tickers" / "GONE.json"
    stale.parent.mkdir()
    stale.write_text("{}")

    _run(tmp_path)

    assert not stale.exists()


def test_export_and_import_carry_the_ticker_reports(ran: Path) -> None:
    tmp_path = ran.parent
    database = str(tmp_path / "published.sqlite")

    main(
        [
            *["import", "--scores", str(tmp_path / "scores.json")],
            *["--tickers", str(tmp_path / "tickers"), "--db", database],
        ]
    )
    main(
        [
            *["export", "--db", database, "--out", str(tmp_path / "again" / "s.json")],
        ]
    )

    again = sorted((tmp_path / "again" / "tickers").glob("*.json"))
    assert [p.name for p in again] == [
        p.name for p in sorted((tmp_path / "tickers").glob("*.json"))
    ]
    assert all(
        a.read_text() == (tmp_path / "tickers" / a.name).read_text() for a in again
    )


@pytest.mark.parametrize(
    ("field", "other"), [("universe", "other"), ("as_of", "2020-01-02")]
)
def test_import_refuses_ticker_reports_that_differ_from_the_scores_file(
    ran: Path, field: str, other: str
) -> None:
    tmp_path = ran.parent
    stray = tmp_path / "tickers" / "AAA.json"
    report = json.loads(next((tmp_path / "tickers").glob("*.json")).read_text())
    report["ticker"], report[field] = "AAA", other
    stray.write_text(json.dumps(report))
    database = tmp_path / "published.sqlite"

    with pytest.raises(SystemExit, match=r"^eq: .*AAA\.json is for "):
        main(
            [
                *["import", "--scores", str(tmp_path / "scores.json")],
                *["--tickers", str(tmp_path / "tickers"), "--db", str(database)],
            ]
        )

    assert not database.exists()


def test_filings_store_the_facts_and_names_without_any_prices(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "db.sqlite"

    main(
        [
            *["filings", "--universe", str(DEMO), "--filings", "synthetic"],
            *["--end", END.isoformat(), "--db", str(database)],
        ]
    )
    capsys.readouterr()
    main(
        [
            *["coverage", "--universe", str(DEMO), "--db", str(database)],
            *["--as-of", END.isoformat()],
        ]
    )

    out = capsys.readouterr().out
    assert "30 of 30 names have at least 10 of" in out
    with Store.open(database, read_only=True) as store:
        assert len(store.read_security(["AAPL", "MSFT"])) == 2
        assert store.read_prices(["SPY"]).empty


def test_metrics_recompute_the_reports_of_stored_facts(ran: Path) -> None:
    tmp_path = ran.parent
    kept = (tmp_path / "tickers" / "AAPL.json").read_text()
    again = tmp_path / "again"

    main(
        [
            *["metrics", "--universe", str(DEMO), "--db", str(tmp_path / "db.sqlite")],
            *["--tickers", str(again)],
        ]
    )

    assert (again / "AAPL.json").read_text() == kept
    assert len(list(again.glob("*.json"))) == 30


def test_coverage_counts_a_name_with_no_stored_filings_as_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "db.sqlite"
    universe = tmp_path / "pair.txt"
    universe.write_text("AAPL,2000-01-03\nMSFT,2000-01-03\n")
    with Store.open(database) as store:
        ingest(SyntheticSource(), store, ["AAPL", "MSFT"], date(2024, 1, 1), END)

    main(["coverage", "--universe", str(universe), "--db", str(database)])

    lines = capsys.readouterr().out.splitlines()
    assert lines[:5] == [
        "Fundamentals coverage, pair universe, no filings, as of 2026-09-30",
        f"0 of 2 names have at least 10 of {len(CONCEPTS)} concepts with a fresh "
        "filed value.",
        "2 with fewer (a missing value stays missing):",
        "  AAPL: 0 (no company at the filings source)",
        "  MSFT: 0 (no company at the filings source)",
    ]
    assert "  revenue: 0" in lines
    assert "    Revenues: 0 fresh, 0 ever  DEAD" in lines


def test_coverage_lists_names_with_few_concepts(
    ran: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tmp_path = ran.parent
    capsys.readouterr()

    main(
        [
            *["coverage", "--universe", str(DEMO), "--db", str(tmp_path / "db.sqlite")],
            *["--min-concepts", "99"],
        ]
    )

    output = capsys.readouterr().out
    assert "demo30" in output
    assert "synthetic" in output
    assert "AAPL" in output


def test_coverage_without_a_database_fails_with_a_message(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match=r"eq: .*does not exist; run `eq run` first"):
        main(["coverage", "--universe", str(DEMO), "--db", str(tmp_path / "none.db")])


def test_a_missing_universe_file_fails_with_a_message(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match=r"eq: .*no-such\.txt"):
        main(["run", "--universe", str(tmp_path / "no-such.txt"), *OFFLINE])


def test_a_network_failure_ends_with_a_message_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TIINGO_API_KEY", "key")
    monkeypatch.setenv("SEC_USER_AGENT", "Mozilla/5.0 Chrome/155.0.0.0 Safari/537.36")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        unused_port = probe.getsockname()[1]
    monkeypatch.setattr(
        "index_core.sources.tiingo.BASE_URL",
        f"http://127.0.0.1:{unused_port}",
        raising=True,
    )

    with pytest.raises(SystemExit, match="eq: could not reach Tiingo"):
        main(
            [
                "run",
                "--universe",
                str(DEMO),
                "--db",
                str(tmp_path / "db.sqlite"),
                "--cache",
                str(tmp_path / "cache"),
            ]
        )


def test_export_can_pick_a_universe_by_name(ran: Path) -> None:
    tmp_path = ran.parent
    database = tmp_path / "db.sqlite"
    small = tmp_path / "small.txt"
    small.write_text(
        "".join(
            f"{ticker},2000-01-03\n"
            for ticker in [
                "AAPL",
                "MSFT",
                "NVDA",
                "AMZN",
                "GOOGL",
                "META",
                "TSLA",
                "JPM",
                "XOM",
                "UNH",
            ]
        )
    )
    main(
        [
            *["run", "--universe", str(small), *OFFLINE, *WINDOW],
            *["--db", str(database), "--out", str(tmp_path / "small.json")],
        ]
    )
    exported = tmp_path / "demo.json"

    main(
        [
            "export",
            "--universe",
            "demo30",
            "--db",
            str(database),
            "--out",
            str(exported),
        ]
    )

    assert len(json.loads(exported.read_text())["rows"]) == 30
    assert len(json.loads((tmp_path / "small.json").read_text())["rows"]) == 10


def test_a_reversed_date_range_fails_with_a_message(tmp_path: Path) -> None:
    with pytest.raises(
        SystemExit, match="eq: start 2026-09-30 is after end 2024-01-01"
    ):
        main(
            [
                *["run", "--universe", str(DEMO), *OFFLINE],
                *["--start", "2026-09-30", "--end", "2024-01-01"],
                *["--db", str(tmp_path / "db.sqlite")],
            ]
        )


def test_backtest_prints_the_out_of_sample_table_with_the_base_rate(
    long_backtest: Path,
) -> None:
    output = (long_backtest / "backtest.txt").read_text()

    assert output.startswith(
        "Walk-forward backtest, members universe, synthetic prices"
    )
    assert "Base rate, share of stocks beating SPY:" in output
    assert "Effective sample: about" in output
    assert "Newey-West t-statistic" in output


def test_a_run_writes_no_evidence_for_a_universe_with_no_backtest(
    long_run: Path,
) -> None:
    assert not (long_run / "evidence.json").exists()


def test_export_publishes_the_stored_backtest_beside_the_scores(
    long_backtest: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = long_backtest / "db.sqlite"
    out = tmp_path / "published" / "scores.json"

    main(["export", "--db", str(database), "--out", str(out)])

    evidence = json.loads((out.parent / "evidence.json").read_text())
    assert evidence["run"]["universe"] == "members"
    assert evidence["run"]["period"] == "development"
    assert evidence["verdict"]["decided"] is False
    assert {m["model"] for m in evidence["models"]} == {
        "lightgbm",
        "ridge",
        "composite",
    }
    assert "members to " in capsys.readouterr().out


def test_export_removes_evidence_it_has_no_backtest_for(
    long_run: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database, out = long_run / "db.sqlite", tmp_path / "published" / "scores.json"
    out.parent.mkdir()
    stale = out.parent / "evidence.json"
    stale.write_text("{}")
    capsys.readouterr()

    main(["export", "--db", str(database), "--out", str(out)])

    assert not stale.exists()
    assert "no backtest stored" in capsys.readouterr().out


def test_export_can_put_the_evidence_elsewhere(
    long_backtest: Path, tmp_path: Path
) -> None:
    database, out = long_backtest / "db.sqlite", tmp_path / "scores.json"
    elsewhere = tmp_path / "elsewhere" / "proof.json"

    main(
        [
            *["export", "--db", str(database), "--out", str(out)],
            *["--evidence", str(elsewhere)],
        ]
    )

    assert elsewhere.exists()
    assert not (out.parent / "evidence.json").exists()


def test_backtest_without_a_database_fails_with_a_message(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match=r"eq: .*does not exist; run `eq run` first"):
        main(["backtest", "--universe", str(DEMO), "--db", str(tmp_path / "none.db")])


def test_backtest_with_too_little_history_fails_with_a_message(ran: Path) -> None:
    database = ran.parent / "db.sqlite"

    with pytest.raises(SystemExit, match=r"eq: .*needs at least \d+.*--start 20"):
        main(["backtest", "--universe", str(DEMO), "--db", str(database)])


DROPPED_ON = date(2019, 7, 1)
ADDED_ON = date(2019, 1, 2)


class _DropStopsTrading(SyntheticSource):
    """DROP has no bars after the day it left, as a delisted stock has none."""

    def fetch(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        if ticker == "DROP":
            end = min(end, DROPPED_ON - timedelta(days=3))
        return super().fetch(ticker, start, end)


@pytest.mark.e2e
@pytest.mark.parametrize("keeps_trading", [True, False], ids=["demoted", "delisted"])
def test_backtest_uses_a_name_only_while_it_is_a_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, keeps_trading: bool
) -> None:
    if not keeps_trading:
        monkeypatch.setitem(cli.PRICES, "synthetic", lambda _: _DropStopsTrading())
    universe = tmp_path / "churn.txt"
    universe.write_text(
        "".join(f"{ticker},2000-01-03\n" for ticker in MEMBERS)
        + f"DROP,2000-01-03,{DROPPED_ON}\n"
        + f"LATE,{ADDED_ON}\n"
    )
    database, predictions = tmp_path / "db.sqlite", tmp_path / "predictions.csv"
    main(
        [
            *["run", "--universe", str(universe), *OFFLINE],
            *["--start", "2014-01-01", "--end", "2024-12-31"],
            *["--db", str(database), "--out", str(tmp_path / "scores.json")],
        ]
    )

    main(
        [
            *["backtest", "--universe", str(universe), "--db", str(database)],
            *["--predictions", str(predictions)],
        ]
    )

    rows = pd.read_csv(predictions, parse_dates=["date"])
    dropped, added = pd.Timestamp(DROPPED_ON), pd.Timestamp(ADDED_ON)
    year = pd.Timedelta(days=365)

    def first_and_last(ticker: str) -> tuple[pd.Timestamp, pd.Timestamp]:
        dates = rows.loc[rows["ticker"] == ticker, "date"]
        return pd.Timestamp(dates.min()), pd.Timestamp(dates.max())

    assert first_and_last("DROP")[0] < dropped - year
    assert first_and_last("DROP")[1] < dropped
    assert first_and_last("LATE")[0] >= added
    assert first_and_last("M00")[1] > dropped + year
    with Store.open(database, read_only=True) as store:
        stored = store.read_prices(["DROP"])["date"].max()
    assert (stored >= pd.Timestamp(DROPPED_ON)) == keeps_trading


@pytest.mark.e2e
def test_backtest_does_not_need_prices_of_a_name_that_left_before_them(
    tmp_path: Path,
) -> None:
    universe = tmp_path / "churn.txt"
    universe.write_text(
        "".join(f"{ticker},2000-01-03\n" for ticker in MEMBERS)
        + "ANCIENT,2000-01-03,2012-01-02\n"
    )
    database = tmp_path / "db.sqlite"
    main(
        [
            *["run", "--universe", str(universe), *OFFLINE],
            *["--start", "2014-01-01", "--end", "2024-12-31"],
            *["--db", str(database), "--out", str(tmp_path / "scores.json")],
        ]
    )

    with Store.open(database, read_only=True) as store:
        assert store.read_prices(["ANCIENT"]).empty

    main(["backtest", "--universe", str(universe), "--db", str(database)])
