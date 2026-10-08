import argparse
import sqlite3
import sys

from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from index_core.backtest_text import render
from index_core.coverage_text import render as render_coverage
from index_core.model import MissingRuntimeError
from index_core.pipeline import (
    HOLDOUT_MONTHS,
    backtest,
    coverage,
    missing_filings,
    run,
)
from index_core.sources.base import SourceError
from index_core.sources.edgar import EdgarSource
from index_core.sources.synthetic import SyntheticFilings, SyntheticSource
from index_core.sources.tiingo import TiingoSource
from index_core.store import Store, StoreError, default_db_path
from index_core.universe import read_universe


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from index_core.report import ScoresReport
    from index_core.sources.base import FilingsSource, PriceSource

DEFAULT_OUT = Path("outputs/scores.json")
HISTORY_DAYS = 6 * 365
MIN_CONCEPTS = 10
DB_HELP = "SQLite file (default: $INDEX_DB or data/index.sqlite)"
PRICES: dict[str, Callable[[], PriceSource]] = {
    "tiingo": TiingoSource.from_env,
    "synthetic": SyntheticSource,
}
FILINGS: dict[str, Callable[[], FilingsSource]] = {
    "edgar": EdgarSource.from_env,
    "synthetic": SyntheticFilings,
}


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
        "--prices",
        choices=list(PRICES),
        default="tiingo",
        help="tiingo reads TIINGO_API_KEY; synthetic is fake prices for offline "
        "runs (default: %(default)s)",
    )
    run_command.add_argument(
        "--filings",
        choices=list(FILINGS),
        default="edgar",
        help="edgar reads SEC_USER_AGENT; synthetic is fake filings for offline "
        "runs (default: %(default)s)",
    )
    run_command.add_argument("--start", type=date.fromisoformat)
    run_command.add_argument("--end", type=date.fromisoformat)
    _add_db_and_out(run_command)

    backtest_command = commands.add_parser(
        "backtest",
        help="walk forward over the stored prices and print out-of-sample metrics",
        description="Reads the prices that `eq run` stored. If they do not reach "
        "back far enough, it says which --start to run with.",
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
    backtest_command.add_argument("--db", type=Path, help=DB_HELP)

    coverage_command = commands.add_parser(
        "coverage",
        help="list names with few fundamentals concepts in the stored filings",
    )
    coverage_command.add_argument("--universe", type=Path, required=True)
    coverage_command.add_argument(
        "--min-concepts",
        type=int,
        default=MIN_CONCEPTS,
        help="a name with fewer concepts is listed (default: %(default)s)",
    )
    coverage_command.add_argument("--db", type=Path, help=DB_HELP)

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
    parser.add_argument("--db", type=Path, help=DB_HELP)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.db is None:
        args.db = default_db_path()
    try:
        if args.command == "run":
            _run(args)
        elif args.command == "backtest":
            _backtest(args)
        elif args.command == "coverage":
            _coverage(args)
        else:
            _export(args)
    except (
        SourceError,
        StoreError,
        MissingRuntimeError,
        ValueError,
        OSError,
        sqlite3.Error,
    ) as error:
        raise SystemExit(f"eq: {error}") from error


def _run(args: argparse.Namespace) -> None:
    tickers = read_universe(args.universe)
    source = PRICES[args.prices]()
    filings = FILINGS[args.filings]()
    fake = [
        kind
        for kind, name in (("prices", args.prices), ("filings", args.filings))
        if name == "synthetic"
    ]
    if fake:
        sys.stderr.write(
            f"eq: synthetic {' and '.join(fake)}; the scores mean nothing\n"
        )

    end = args.end or date.today()
    start = args.start or end - timedelta(days=HISTORY_DAYS)
    with Store.open(args.db) as store:
        report = run(source, filings, store, args.universe.stem, tickers, start, end)
        missing = missing_filings(store, tickers)
    if missing:
        sys.stderr.write(
            f"eq: no filings for {len(missing)} tickers ({', '.join(missing)}); "
            "their fundamentals are missing\n"
        )
    _write(report, args.out)


def _coverage(args: argparse.Namespace) -> None:
    tickers = read_universe(args.universe)
    _require_database(args.db)
    with Store.open(args.db, read_only=True) as store:
        result = coverage(store, tickers)
    sys.stdout.write(render_coverage(result, args.universe.stem, args.min_concepts))


def _backtest(args: argparse.Namespace) -> None:
    tickers = read_universe(args.universe)
    _require_database(args.db)
    with Store.open(args.db, read_only=True) as store:
        result = backtest(
            store,
            tickers,
            args.first_oos,
            args.holdout_months,
            final=args.final,
        )
    sys.stdout.write(render(result, args.universe.stem))


def _require_database(path: Path) -> None:
    if path.exists():
        return
    others = sorted(other.name for other in path.parent.glob("*.sqlite"))
    advice = (
        f"found {', '.join(others)} beside it; pass one with --db"
        if others
        else "run `eq run` first"
    )
    raise StoreError(f"{path} does not exist; {advice}")


def _export(args: argparse.Namespace) -> None:
    _require_database(args.db)
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
