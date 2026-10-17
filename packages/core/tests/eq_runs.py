"""Runs of `eq` that CLI and API tests share.

A run over the demo universe takes tens of seconds. Each fixture here runs the
real command once per session. A test gets a copy, so what it changes stays its
own.
"""

import contextlib
import io
import shutil

from pathlib import Path

import pytest

from index_cli.main import main


DEMO = Path(__file__).parents[3] / "universes" / "demo30.txt"
OFFLINE = ["--prices", "synthetic", "--filings", "synthetic"]
# Just over the days that a backtest with the default holdout needs.
LONG_START = "2020-06-01"
LONG_MEMBERS = 12


def _run(directory: Path, start: str, universe: Path = DEMO) -> Path:
    main(
        [
            *["run", "--universe", str(universe), *OFFLINE],
            *["--start", start, "--end", "2026-09-30"],
            *["--db", str(directory / "db.sqlite")],
            *["--out", str(directory / "scores.json")],
        ]
    )
    return directory


def _copy(source: Path, target: Path) -> Path:
    shutil.copytree(source, target, dirs_exist_ok=True)
    return target


@pytest.fixture(scope="session")
def demo_run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """`eq run` over the demo universe from 2024: a database, `scores.json` and
    `tickers/`."""
    return _run(tmp_path_factory.mktemp("demo"), "2024-01-01")


@pytest.fixture
def ran(demo_run: Path, tmp_path: Path) -> Path:
    """`scores.json` of a copy of `demo_run` in the test's own directory."""
    return _copy(demo_run, tmp_path) / "scores.json"


@pytest.fixture(scope="session")
def long_run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """`eq run` from `LONG_START`, long enough to backtest, with no backtest
    stored. The universe `members.txt` beside the database has `LONG_MEMBERS`
    names: more than the `MIN_NAMES` a month of the backtest needs, at under half
    the cost of the demo universe."""
    directory = tmp_path_factory.mktemp("long")
    universe = directory / "members.txt"
    universe.write_text("".join(f"M{i:02d},2000-01-03\n" for i in range(LONG_MEMBERS)))
    return _run(directory, LONG_START, universe)


@pytest.fixture(scope="session")
def long_backtest(long_run: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """`long_run` after `eq backtest`, with what it printed in `backtest.txt`."""
    directory = _copy(long_run, tmp_path_factory.mktemp("backtested"))
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        main(
            [
                *["backtest", "--universe", str(directory / "members.txt")],
                *["--db", str(directory / "db.sqlite")],
            ]
        )
    (directory / "backtest.txt").write_text(printed.getvalue())
    return directory
