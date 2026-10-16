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
    DEFAULT_COST,
    HOLDOUT_MONTHS,
    backtest,
    compute_metrics,
    coverage,
    ingest_factors,
    ingest_filings,
    missing_filings,
    run,
)
from index_core.report import ScoresReport, TickerReport
from index_core.sources.base import SourceError
from index_core.sources.edgar import EdgarSource
from index_core.sources.french import FamaFrenchSource
from index_core.sources.synthetic import (
    SyntheticFactors,
    SyntheticFilings,
    SyntheticSource,
)
from index_core.sources.tiingo import TiingoSource
from index_core.store import Store, StoreError, default_db_path
from index_core.universe import read_universe


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from index_core.sources.base import FactorSource, FilingsSource, PriceSource

DEFAULT_OUT = Path("outputs/scores.json")
DEFAULT_TICKERS = Path("outputs/tickers")
HISTORY_DAYS = 6 * 365
MIN_CONCEPTS = 10
MISSING_SHOWN = 10
DB_HELP = "SQLite file (default: $INDEX_DB or data/index.sqlite)"
DEFAULT_CACHE = Path("data/cache")
# Each factory takes the directory where a source keeps its responses. A
# filings factory also takes the SEC identifiers the universe lists.
PRICES: dict[str, Callable[[Path], PriceSource]] = {
    "tiingo": TiingoSource.from_env,
    "synthetic": lambda _: SyntheticSource(),
}
FILINGS: dict[str, Callable[[Path, Mapping[str, tuple[int, ...]]], FilingsSource]] = {
    "edgar": EdgarSource.from_env,
    "synthetic": lambda _, __: SyntheticFilings(),
}
FACTORS: dict[str, Callable[[Path], FactorSource]] = {
    "french": FamaFrenchSource.from_env,
    "synthetic": lambda _: SyntheticFactors(),
}
BPS = 1e4


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
    run_command.add_argument(
        "--cache",
        type=Path,
        default=DEFAULT_CACHE,
        help="directory where Tiingo and EDGAR responses are kept for a day, so "
        "a run that stops can resume (default: %(default)s)",
    )
    _add_db_and_out(run_command)
    _add_tickers(run_command)

    filings_command = commands.add_parser(
        "filings",
        help="fetch the filed facts and company names of a universe, without prices",
        description="Stores every member's facts and identity, replacing those "
        "stored before. `eq coverage --as-of` reports what they hold.",
    )
    filings_command.add_argument("--universe", type=Path, required=True)
    filings_command.add_argument(
        "--filings",
        choices=list(FILINGS),
        default="edgar",
        help="edgar reads SEC_USER_AGENT; synthetic is fake filings for offline "
        "runs (default: %(default)s)",
    )
    filings_command.add_argument("--start", type=date.fromisoformat)
    filings_command.add_argument("--end", type=date.fromisoformat)
    filings_command.add_argument(
        "--cache",
        type=Path,
        default=DEFAULT_CACHE,
        help="directory where EDGAR responses are kept for a day, so a run that "
        "stops can resume (default: %(default)s)",
    )
    filings_command.add_argument("--db", type=Path, help=DB_HELP)

    metrics_command = commands.add_parser(
        "metrics",
        help="compute the fundamentals metrics and ticker reports from stored facts",
        description="Reads the facts that `eq run` or `eq filings` stored and the "
        "dates of the stored SPY prices. `eq run` does this itself.",
    )
    metrics_command.add_argument("--universe", type=Path, required=True)
    metrics_command.add_argument("--db", type=Path, help=DB_HELP)
    _add_tickers(metrics_command, beside_out=False)

    factors_command = commands.add_parser(
        "factors",
        help="fetch the Fama-French five-factor daily returns",
        description="Stores the whole file, replacing any stored before. "
        "`eq backtest` regresses the strategy's returns on it.",
    )
    factors_command.add_argument(
        "--source",
        choices=list(FACTORS),
        default="french",
        help="french downloads Kenneth French's data library file; synthetic is "
        "fake returns for offline runs (default: %(default)s)",
    )
    factors_command.add_argument(
        "--cache",
        type=Path,
        default=DEFAULT_CACHE,
        help="directory where the download is kept for a day (default: %(default)s)",
    )
    factors_command.add_argument("--db", type=Path, help=DB_HELP)

    backtest_command = commands.add_parser(
        "backtest",
        help="walk forward over the stored prices and print out-of-sample metrics",
        description="Reads the prices that `eq run` stored. If they do not reach "
        "back far enough, it says which --start to run with. Each model's net "
        "spread is added to the database's trial ledger.",
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
        "--cost-bps",
        type=float,
        default=DEFAULT_COST * BPS,
        help="loss on each trade's notional, one way, in basis points. An assumed "
        "figure; the output also gives the break-even cost (default: %(default)g)",
    )
    backtest_command.add_argument(
        "--predictions",
        type=Path,
        help="write the measured out-of-sample rows (date, ticker, lightgbm, ridge, "
        "composite, label, excess) as CSV",
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
    coverage_command.add_argument(
        "--as-of",
        type=date.fromisoformat,
        help="the date to judge freshness on (default: the latest stored SPY price)",
    )
    coverage_command.add_argument("--db", type=Path, help=DB_HELP)

    export_command = commands.add_parser(
        "export", help="write the latest stored scores and ticker reports as JSON"
    )
    export_command.add_argument(
        "--universe",
        help="universe name, the file name without extension (default: latest run)",
    )
    _add_db_and_out(export_command)
    _add_tickers(export_command)

    import_command = commands.add_parser(
        "import",
        help="store scores and ticker reports from JSON files, for the API to serve",
        description="Reads the files that `eq run` or `eq export` wrote and stores "
        "them as the latest run, so the API can serve them without prices.",
    )
    import_command.add_argument("--scores", type=Path, required=True)
    import_command.add_argument(
        "--tickers",
        type=Path,
        help="directory of ticker report files to store as well",
    )
    import_command.add_argument("--db", type=Path, help=DB_HELP)
    return parser


def _add_db_and_out(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", type=Path, help=DB_HELP)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)


def _add_tickers(parser: argparse.ArgumentParser, *, beside_out: bool = True) -> None:
    parser.add_argument(
        "--tickers",
        type=Path,
        help="directory for the ticker reports, one JSON file per ticker, replacing "
        "those there (default: "
        + ("`tickers` beside --out" if beside_out else str(DEFAULT_TICKERS))
        + ")",
    )


def _tickers_dir(args: argparse.Namespace) -> Path:
    tickers: Path | None = args.tickers
    if tickers is not None:
        return tickers
    out: Path = getattr(args, "out", DEFAULT_OUT)
    return out.parent / "tickers"


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.db is None:
        args.db = default_db_path()
    try:
        if args.command == "run":
            _run(args)
        elif args.command == "factors":
            _factors(args)
        elif args.command == "backtest":
            _backtest(args)
        elif args.command == "filings":
            _filings(args)
        elif args.command == "metrics":
            _metrics(args)
        elif args.command == "coverage":
            _coverage(args)
        elif args.command == "import":
            _import(args)
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
    universe = read_universe(args.universe)
    source = PRICES[args.prices](args.cache)
    filings = FILINGS[args.filings](args.cache, universe.ciks)
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
        report = run(source, filings, store, universe, start, end)
        reports = store.ticker_reports(report.universe)
    for reason in ("prices", "filings"):
        tickers = [i.ticker for i in report.left_out if i.reason == f"no {reason}"]
        _warn_missing(reason, tickers, "they are left out")
    _write(report, args.out)
    _write_tickers(reports, _tickers_dir(args))


def _filings(args: argparse.Namespace) -> None:
    universe = read_universe(args.universe)
    filings = FILINGS[args.filings](args.cache, universe.ciks)
    if args.filings == "synthetic":
        sys.stderr.write("eq: synthetic filings; the figures mean nothing\n")
    end = args.end or date.today()
    start = args.start or end - timedelta(days=HISTORY_DAYS)
    tickers = universe.members_between(start, end)
    with Store.open(args.db) as store:
        store.require_filings(filings.name)
        ingest_filings(filings, store, tickers, end, universe.sectors)
        unfiled = missing_filings(store, tickers)
    _warn_missing("filings", unfiled, "they hold no facts")
    sys.stdout.write(
        f"stored the filings of {len(tickers) - len(unfiled)} of {len(tickers)} "
        f"tickers as filed up to {end} in {args.db}\n"
    )


def _metrics(args: argparse.Namespace) -> None:
    universe = read_universe(args.universe)
    _require_database(args.db)
    with Store.open(args.db) as store:
        as_of = compute_metrics(store, universe)
        reports = store.ticker_reports(universe.name)
    sys.stdout.write(f"computed the metrics of {universe.name} as of {as_of.date()}\n")
    _write_tickers(reports, _tickers_dir(args))


def _warn_missing(what: str, tickers: list[str], effect: str) -> None:
    if not tickers:
        return
    shown = ", ".join(tickers[:MISSING_SHOWN])
    more = (
        f" and {len(tickers) - MISSING_SHOWN} more"
        if len(tickers) > MISSING_SHOWN
        else ""
    )
    sys.stderr.write(
        f"eq: no {what} for {len(tickers)} tickers ({shown}{more}); {effect}\n"
    )


def _coverage(args: argparse.Namespace) -> None:
    universe = read_universe(args.universe)
    _require_database(args.db)
    with Store.open(args.db, read_only=True) as store:
        result = coverage(store, universe, args.as_of)
    sys.stdout.write(render_coverage(result, args.universe.stem, args.min_concepts))


def _factors(args: argparse.Namespace) -> None:
    source = FACTORS[args.source](args.cache)
    if args.source == "synthetic":
        sys.stderr.write("eq: synthetic factors; the alpha means nothing\n")
    with Store.open(args.db) as store:
        ingest_factors(source, store)
        days = store.read_factors().index
    sys.stdout.write(
        f"stored {len(days)} days of {source.name} factors, "
        f"{days.min().date()} to {days.max().date()}, in {args.db}\n"
    )


def _backtest(args: argparse.Namespace) -> None:
    universe = read_universe(args.universe)
    _require_database(args.db)
    # Writable: the run adds its specifications to the trial ledger.
    with Store.open(args.db) as store:
        result = backtest(
            store,
            universe,
            args.first_oos,
            args.holdout_months,
            args.cost_bps / BPS,
            final=args.final,
        )
    if args.predictions is not None:
        args.predictions.parent.mkdir(parents=True, exist_ok=True)
        result.predictions.to_csv(args.predictions)
    sys.stdout.write(render(result, universe.name))


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
        reports = [] if report is None else store.ticker_reports(report.universe)
    if report is None:
        raise StoreError(f"{args.db} holds no matching scores; run `eq run` first")
    _write(report, args.out)
    _write_tickers(reports, _tickers_dir(args))


def _import(args: argparse.Namespace) -> None:
    report = ScoresReport.model_validate_json(args.scores.read_text())
    reports = []
    for path in [] if args.tickers is None else sorted(args.tickers.glob("*.json")):
        ticker_report = TickerReport.model_validate_json(path.read_text())
        if (ticker_report.universe, ticker_report.as_of) != (
            report.universe,
            report.as_of,
        ):
            raise StoreError(
                f"{path} is for {ticker_report.universe} as of "
                f"{ticker_report.as_of}, not {report.universe} as of "
                f"{report.as_of} like {args.scores}"
            )
        reports.append(ticker_report)
    with Store.open(args.db) as store:
        store.save_report(report)
        store.add_ticker_reports(reports)
    sys.stdout.write(
        f"stored {len(report.rows)} scores as of {report.as_of} and "
        f"{len(reports)} ticker reports in {args.db}\n"
    )


def _write_tickers(reports: list[TickerReport], directory: Path) -> None:
    """Replace the `.json` files of `directory` with one file per report, on one
    line: a report holds every filed figure and is read by programs."""
    directory.mkdir(parents=True, exist_ok=True)
    for stale in directory.glob("*.json"):
        stale.unlink()
    for report in reports:
        (directory / f"{report.ticker}.json").write_text(
            report.model_dump_json() + "\n"
        )
    sys.stdout.write(f"wrote {len(reports)} ticker reports to {directory}\n")


def _write(report: ScoresReport, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.model_dump_json(indent=2) + "\n")
    sys.stdout.write(f"wrote {len(report.rows)} scores as of {report.as_of} to {out}\n")


if __name__ == "__main__":
    main()
