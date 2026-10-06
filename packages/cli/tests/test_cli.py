import json

from datetime import date
from pathlib import Path

import pytest

from index_cli.main import main
from index_core.pipeline import ingest
from index_core.sources.synthetic import SyntheticSource
from index_core.store import Store


DEMO = Path(__file__).parents[3] / "universes" / "demo30.txt"
END = date(2026, 9, 30)
WINDOW = ["--start", "2024-01-01", "--end", END.isoformat()]


def _run(tmp_path: Path) -> Path:
    out = tmp_path / "scores.json"
    main(
        [
            *["run", "--universe", str(DEMO), "--source", "synthetic", *WINDOW],
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


def test_run_writes_thirty_scored_rows_with_an_as_of_date(tmp_path: Path) -> None:
    scores = json.loads(_run(tmp_path).read_text())

    assert scores["as_of"] == "2026-09-30"
    assert scores["status"] == "experimental, not validated"
    assert scores["source"] == "synthetic"
    rows = scores["rows"]
    assert len(rows) == 30
    assert [row["rank"] for row in rows] == list(range(1, 31))
    assert all(
        isinstance(row["score"], int) and 1 <= row["score"] <= 10 for row in rows
    )
    assert len({row["ticker"] for row in rows}) == 30


def test_export_rewrites_the_stored_scores_unchanged(tmp_path: Path) -> None:
    first = _run(tmp_path)
    exported = tmp_path / "exported.json"

    main(["export", "--db", str(tmp_path / "db.sqlite"), "--out", str(exported)])

    assert exported.read_text() == first.read_text()


def test_export_without_a_database_fails_with_a_message(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="does not exist"):
        main(["export", "--db", str(tmp_path / "none.sqlite")])


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


def test_coverage_counts_a_name_with_no_stored_filings_as_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "db.sqlite"
    universe = tmp_path / "pair.txt"
    universe.write_text("AAPL\nMSFT\n")
    with Store.open(database) as store:
        ingest(SyntheticSource(), store, ["AAPL", "MSFT"], date(2024, 1, 1), END)

    main(["coverage", "--universe", str(universe), "--db", str(database)])

    assert capsys.readouterr().out == (
        "Fundamentals coverage, pair universe, no filings, as of 2026-09-30\n"
        "0 of 2 names have at least 10 of 13 concepts with a fresh filed value.\n"
        "2 with fewer (a missing value stays missing):\n"
        "  AAPL: 0\n"
        "  MSFT: 0\n"
    )


def test_coverage_lists_names_with_few_concepts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(tmp_path)
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
        main(
            [
                "run",
                "--universe",
                str(tmp_path / "no-such.txt"),
                "--source",
                "synthetic",
            ]
        )


def test_a_network_failure_ends_with_a_message_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TIINGO_API_KEY", "key")
    monkeypatch.setenv("SEC_USER_AGENT", "Jane Doe jane@example.com")
    monkeypatch.setattr(
        "index_core.sources.tiingo.BASE_URL", "http://127.0.0.1:1", raising=True
    )

    with pytest.raises(SystemExit, match="eq: could not reach Tiingo"):
        main(["run", "--universe", str(DEMO), "--db", str(tmp_path / "db.sqlite")])


def test_export_can_pick_a_universe_by_name(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite"
    small = tmp_path / "small.txt"
    small.write_text("AAPL\nMSFT\nNVDA\nAMZN\nGOOGL\nMETA\nTSLA\nJPM\nXOM\nUNH\n")
    _run(tmp_path)
    main(
        [
            *["run", "--universe", str(small), "--source", "synthetic", *WINDOW],
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
                *["run", "--universe", str(DEMO), "--source", "synthetic"],
                *["--start", "2026-09-30", "--end", "2024-01-01"],
                *["--db", str(tmp_path / "db.sqlite")],
            ]
        )


def test_backtest_prints_the_out_of_sample_table_with_the_base_rate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "db.sqlite"
    main(
        [
            *["run", "--universe", str(DEMO), "--source", "synthetic"],
            *["--start", "2016-01-01", "--end", "2026-09-30"],
            *["--db", str(database), "--out", str(tmp_path / "scores.json")],
        ]
    )
    capsys.readouterr()

    main(["backtest", "--universe", str(DEMO), "--db", str(database)])

    output = capsys.readouterr().out
    assert output.startswith("Walk-forward backtest, demo30 universe, synthetic prices")
    assert "Base rate, share of stocks beating SPY:" in output
    assert "Effective sample: about" in output
    assert "Newey-West t-statistic" in output


def test_backtest_without_a_database_fails_with_a_message(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match=r"eq: .*does not exist; run `eq run` first"):
        main(["backtest", "--universe", str(DEMO), "--db", str(tmp_path / "none.db")])


def test_backtest_with_too_little_history_fails_with_a_message(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite"
    main(
        [
            *["run", "--universe", str(DEMO), "--source", "synthetic", *WINDOW],
            *["--db", str(database), "--out", str(tmp_path / "scores.json")],
        ]
    )

    with pytest.raises(SystemExit, match=r"eq: .*too short"):
        main(["backtest", "--universe", str(DEMO), "--db", str(database)])
