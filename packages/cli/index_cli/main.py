import argparse
import sqlite3
import sys

from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from index_core.backtest_text import render
from index_core.pipeline import HOLDOUT_MONTHS, backtest, run
from index_core.sources.base import PriceSource, SourceError
from index_core.sources.synthetic import SyntheticSource
from index_core.sources.tiingo import TiingoSource
from index_core.store import Store, StoreError, default_db_path
from index_core.universe import read_universe


if TYPE_CHECKING:
    from collections.abc import Sequence

    from index_core.report import ScoresReport

DEFAULT_OUT = Path("outputs/scores.json")
HISTORY_DAYS = 6 * 365


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eq",
        description="index: rank US stocks with an experimental, unvalidated score.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run_command = commands.add_parser(
        "run", help="fetch prices, fit the model, score and export"
    )
    run_command.add_argument("--universe", type=Path, required=True)
    run_command.add_argument(
        "--source",
        choices=["tiingo", "synthetic"],
        default="tiingo",
        help="tiingo reads TIINGO_API_KEY; synthetic is fake data for offline runs",
    )
    run_command.add_argument("--start", type=date.fromisoformat)
    run_command.add_argument("--end", type=date.fromisoformat)
    _add_db_and_out(run_command)

    backtest_command = commands.add_parser(
        "backtest",
        help="walk forward over the stored prices and print out-of-sample metrics",
        description="Reads the prices that `eq run` stored, so run it first with "
        "a --start early enough for several years of training.",
    )
    backtest_command.add_argument("--universe", type=Path, required=True)
    backtest_command.add_argument(
        "--first-oos",
        type=date.fromisoformat,
        help="first test quarter begins on or after this date (default: as soon "
        "as there is enough training history)",
    )
    backtest_command.add_argument(
        "--holdout-months",
        type=int,
        default=HOLDOUT_MONTHS,
        help="months of predictions held back from the metrics (default: %(default)s)",
    )
    backtest_command.add_argument(
        "--final",
        action="store_true",
        help="measure the held-back months instead. Look once, before a public claim",
    )
    backtest_command.add_argument(
        "--db",
        type=Path,
        default=default_db_path(),
        help="SQLite file (default: $INDEX_DB or data/index.sqlite)",
    )

    export_command = commands.add_parser(
        "export", help="write the latest stored scores as JSON"
    )
    export_command.add_argument(
        "--universe",
        help="universe name, the file name without extension (default: latest run)",
    )
    _add_db_and_out(export_command)
    return parser


def _add_db_and_out(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db",
        type=Path,
        default=default_db_path(),
        help="SQLite file (default: $INDEX_DB or data/index.sqlite)",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "run":
            _run(args)
        elif args.command == "backtest":
            _backtest(args)
        else:
            _export(args)
    except (SourceError, StoreError, ValueError, OSError, sqlite3.Error) as error:
        raise SystemExit(f"eq: {error}") from error


def _run(args: argparse.Namespace) -> None:
    tickers = read_universe(args.universe)
    source: PriceSource
    if args.source == "synthetic":
        source = SyntheticSource()
        sys.stderr.write("eq: synthetic prices; the scores mean nothing\n")
    else:
        source = TiingoSource.from_env()

    end = args.end or date.today()
    start = args.start or end - timedelta(days=HISTORY_DAYS)
    with Store.open(args.db) as store:
        report = run(source, store, args.universe.stem, tickers, start, end)
    _write(report, args.out)


def _backtest(args: argparse.Namespace) -> None:
    tickers = read_universe(args.universe)
    if not args.db.exists():
        raise StoreError(f"{args.db} does not exist; run `eq run` first")
    with Store.open(args.db, read_only=True) as store:
        result = backtest(
            store,
            tickers,
            args.first_oos,
            args.holdout_months,
            final=args.final,
        )
    sys.stdout.write(render(result, args.universe.stem))


def _export(args: argparse.Namespace) -> None:
    if not args.db.exists():
        raise StoreError(f"{args.db} does not exist")
    with Store.open(args.db, read_only=True) as store:
        report = store.latest_report(args.universe)
    if report is None:
        raise StoreError(f"{args.db} holds no matching scores; run `eq run` first")
    _write(report, args.out)


def _write(report: ScoresReport, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.model_dump_json(indent=2) + "\n")
    sys.stdout.write(f"wrote {len(report.rows)} scores as of {report.as_of} to {out}\n")


if __name__ == "__main__":
    main()
