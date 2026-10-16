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
# Leaves MIN_TRAIN_DATES training dates before the first out-of-sample day.
FIRST_OOS = "2022-07-01"


def _run(directory: Path, start: str) -> Path:
    main(
        [
            *["run", "--universe", str(DEMO), *OFFLINE],
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
    """`eq run` from 2019, long enough to backtest from `FIRST_OOS`, with no
    backtest stored."""
    return _run(tmp_path_factory.mktemp("long"), "2019-01-01")


@pytest.fixture(scope="session")
def long_backtest(long_run: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """`long_run` after `eq backtest`, with what it printed in `backtest.txt`."""
    directory = _copy(long_run, tmp_path_factory.mktemp("backtested"))
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        main(
            [
                *["backtest", "--universe", str(DEMO)],
                *["--db", str(directory / "db.sqlite")],
                *["--first-oos", FIRST_OOS],
            ]
        )
    (directory / "backtest.txt").write_text(printed.getvalue())
    return directory
