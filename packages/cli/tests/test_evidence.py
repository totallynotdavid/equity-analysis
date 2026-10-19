"""`eq backtest` as a user runs it: the table, the costs, the ledger, the verdict.

The prices carry a planted signal (or none), so what the table should say is
known beforehand.
"""

import io
import re
import shutil
import zipfile

from datetime import date
from typing import TYPE_CHECKING

import httpx2
import pandas as pd
import pytest

from index_cli import main as cli
from index_cli.main import main
from index_core.pipeline import ingest, ingest_filings
from index_core.sources.french import FamaFrenchSource
from index_core.sources.synthetic import SyntheticFilings, SyntheticSource
from index_core.store import Store
from membership import always
from planted import planted_prices


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

TICKERS = [f"T{i:02d}" for i in range(14)]
END = date(2023, 3, 31)
FIRST_OOS = "2019-01-01"
BACKTEST = ["--first-oos", FIRST_OOS, "--holdout-months", "12"]
FEATURES = 34


class _Planted(SyntheticSource):
    """Serves a fixed price frame instead of the synthetic walk."""

    def __init__(self, frame: pd.DataFrame) -> None:
        self._bars = {
            str(ticker): bars.set_index("date").drop(columns="ticker")
            for ticker, bars in frame.groupby("ticker")
        }

    def fetch(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        bars = self._bars[ticker]
        return bars[
            (bars.index >= pd.Timestamp(start)) & (bars.index <= pd.Timestamp(end))
        ]


def _database(path: Path, signal: float) -> Path:
    source = _Planted(planted_prices(tickers=len(TICKERS), signal=signal))
    with Store.open(path) as store:
        ingest(source, store, always(TICKERS), date(2012, 1, 2), END)
        ingest_filings(SyntheticFilings(), store, TICKERS, END)
    return path


@pytest.fixture(scope="module")
def signal_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _database(tmp_path_factory.mktemp("signal") / "db.sqlite", signal=1.0)


@pytest.fixture(scope="module")
def noise_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _database(tmp_path_factory.mktemp("noise") / "db.sqlite", signal=0.0)


@pytest.fixture
def universe(tmp_path: Path) -> Path:
    path = tmp_path / "planted.txt"
    path.write_text("".join(f"{ticker},2000-01-03\n" for ticker in TICKERS))
    return path


@pytest.fixture(scope="module")
def printed() -> dict[tuple[Path, tuple[str, ...]], str]:
    """What a run on a fresh copy of a database printed, by database and options.
    A run on a fresh copy depends on nothing else, so tests share the output
    instead of repeating a backtest that takes seconds."""
    return {}


@pytest.fixture
def backtest(
    tmp_path: Path,
    universe: Path,
    capsys: pytest.CaptureFixture[str],
    printed: dict[tuple[Path, tuple[str, ...]], str],
) -> Callable[..., str]:
    """Run `eq backtest` on a copy of a database and return what it printed.

    `keep` runs on that file itself, so its ledger carries over. `again` runs
    on a fresh copy even when the same run was printed before.
    """
    copies = 0

    def run(
        database: Path,
        *options: str,
        keep: Path | None = None,
        again: bool = False,
    ) -> str:
        nonlocal copies
        key = (database, options)
        if keep is None and not again and key in printed:
            return printed[key]
        target = keep
        if target is None:
            copies += 1
            target = tmp_path / f"copy{copies}.sqlite"
            shutil.copy(database, target)
        capsys.readouterr()
        main(
            [
                *["backtest", "--universe", str(universe), "--db", str(target)],
                *BACKTEST,
                *options,
            ]
        )
        output = capsys.readouterr().out
        if keep is None:
            printed[key] = output
        return output

    return run


def _section(output: str, title: str) -> list[str]:
    """The lines under the line that starts with `title`, to the next blank."""
    lines = output.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(title))
    block = lines[start + 1 :]
    return block[: block.index("")]


def _cells(output: str, section: str, row: str) -> list[str]:
    (line,) = (x for x in _section(output, section) if x.startswith(row))
    return line.split()


def _net_ladder(output: str, model: str = "LightGBM") -> list[float]:
    cells = _cells(output, "Net decile spread a year by one-way cost", model)
    return [float(cell.rstrip("%")) / 100 for cell in cells[1:]]


def _portfolio(output: str, label: str) -> list[str]:
    return _cells(output, "Portfolios, rebalanced monthly", f"LightGBM    {label}")


def _ledger(output: str) -> int:
    match = re.search(r"ledger of this database counts (\d+) specifications", output)
    assert match
    return int(match.group(1))


@pytest.mark.e2e
def test_a_planted_signal_shows_up_in_every_part_of_the_table(
    backtest: Callable[..., str], signal_db: Path
) -> None:
    output = backtest(signal_db)

    model = _cells(output, "Models, on the same rows", "LightGBM")
    assert float(model[1]) > 0.05
    assert float(model[2].strip("[,")) > 0
    assert float(model[4]) > 3
    top_minus_bottom = _portfolio(output, "top - bottom")
    gross, net = top_minus_bottom[4], top_minus_bottom[5]
    assert float(gross.rstrip("%")) > 0
    assert float(net.rstrip("%")) > 0
    features = _section(output, "Rank IC of each feature alone")
    assert len(features) - 1 == FEATURES
    # The table is sorted by |t|, so the first row is the strongest feature.
    assert abs(float(features[1].split()[-2])) > 3


@pytest.mark.e2e
def test_prices_with_no_signal_give_no_rank_ic_and_the_rule_refuses_the_score(
    backtest: Callable[..., str], noise_db: Path
) -> None:
    output = backtest(noise_db, "--final")

    model = _cells(output, "Models, on the same rows", "LightGBM")
    assert abs(float(model[4])) < 2
    assert float(model[2].strip("[,")) < 0 < float(model[3].strip("]"))
    assert "Verdict: do not show the score." in output
    # A short holdout of noise can still give a spread interval clear of zero,
    # so the rule needs the IC and the Deflated Sharpe checks beside it.
    checks = [x for x in output.splitlines() if x.startswith("  [")]
    assert len(checks) == 3
    assert any(x.startswith("  [ ]") for x in checks)


def test_the_development_period_prints_no_verdict(
    backtest: Callable[..., str], signal_db: Path
) -> None:
    output = backtest(signal_db)

    assert "Verdict: none." in output
    assert "run with --final" in output


@pytest.mark.e2e
def test_the_final_period_ends_in_a_verdict(
    backtest: Callable[..., str], signal_db: Path
) -> None:
    output = backtest(signal_db, "--final")

    assert re.search(r"^Verdict: (show|do not show) the score\.$", output, re.M)
    assert "Period: holdout" in output
    # The candidate comes from the development months, which the table names.
    chosen = _section(output, "Candidate:")
    assert "Development months, which chose it" in chosen[0]
    assert "Held-out months, which test it:" in chosen[4]


def test_raising_the_cost_lowers_the_net_return_at_every_step(
    backtest: Callable[..., str], signal_db: Path
) -> None:
    output = backtest(signal_db)

    ladder = _net_ladder(output)

    assert len(ladder) == 5
    assert ladder == sorted(ladder, reverse=True)
    assert len(set(ladder)) == len(ladder)


@pytest.mark.e2e
def test_the_net_return_of_a_run_at_a_cost_matches_the_ladder_of_another(
    backtest: Callable[..., str], signal_db: Path
) -> None:
    ladder_run = backtest(signal_db, "--cost-bps", "10")
    direct = backtest(signal_db, "--cost-bps", "50")

    assert "10 bps one way" in ladder_run
    assert "50 bps one way" in direct
    at_fifty = _net_ladder(ladder_run)[-1]
    assert float(_portfolio(direct, "top - bottom")[5].rstrip("%")) / 100 == at_fifty


@pytest.mark.e2e
def test_a_higher_cost_option_lowers_the_net_column_of_the_portfolio_table(
    backtest: Callable[..., str], signal_db: Path
) -> None:
    nets = [
        float(
            _portfolio(backtest(signal_db, "--cost-bps", bps), "top - bottom")[
                5
            ].rstrip("%")
        )
        for bps in ("0", "10", "30", "100")
    ]

    assert nets == sorted(nets, reverse=True)
    assert len(set(nets)) == 4


@pytest.mark.e2e
def test_the_break_even_cost_is_the_same_on_every_run_and_at_every_cost(
    backtest: Callable[..., str], signal_db: Path
) -> None:
    first = backtest(signal_db, "--cost-bps", "10")
    again = backtest(signal_db, "--cost-bps", "10", again=True)
    other_cost = backtest(signal_db, "--cost-bps", "40")

    def break_evens(output: str) -> list[str]:
        rows = _section(output, "Portfolios, rebalanced monthly")[1:]
        return [re.split(r"\s{2,}", row.strip())[-1] for row in rows]

    assert break_evens(first) == break_evens(again)
    assert break_evens(first) == break_evens(other_cost)
    assert all("bps" in row or "none" in row for row in break_evens(first))


@pytest.mark.e2e
def test_a_cost_at_the_break_even_leaves_nothing_of_the_edge(
    backtest: Callable[..., str], signal_db: Path
) -> None:
    output = backtest(signal_db)
    cells = _portfolio(output, "top - bottom")
    break_even_bps = float(cells[cells.index("bps") - 1])

    at_break_even = backtest(signal_db, "--cost-bps", str(break_even_bps))

    net = float(_portfolio(at_break_even, "top - bottom")[5].rstrip("%"))
    assert abs(net) < 0.2


@pytest.mark.e2e
def test_every_new_specification_adds_to_the_ledger_and_a_repeat_does_not(
    tmp_path: Path, backtest: Callable[..., str], signal_db: Path
) -> None:
    database = tmp_path / "ledger.sqlite"
    shutil.copy(signal_db, database)

    def count(*options: str) -> int:
        return _ledger(backtest(signal_db, *options, keep=database))

    assert count() == 3
    assert count() == 3
    assert count("--cost-bps", "25") == 6
    assert count("--cost-bps", "25") == 6
    assert count("--holdout-months", "10") == 9
    assert count("--final") == 12
    assert count("--first-oos", "2019-07-01") == 15


@pytest.mark.e2e
def test_more_trials_lower_the_probability_that_the_edge_is_not_luck(
    tmp_path: Path, backtest: Callable[..., str], signal_db: Path
) -> None:
    database = tmp_path / "ledger.sqlite"
    shutil.copy(signal_db, database)

    def lightgbm_probability(output: str) -> float:
        return float(_cells(output, "Multiple testing", "LightGBM")[-1])

    alone = lightgbm_probability(backtest(signal_db, keep=database))
    for options in (["--final"], ["--cost-bps", "40"], ["--holdout-months", "10"]):
        backtest(signal_db, *options, keep=database)
    crowded = lightgbm_probability(backtest(signal_db, keep=database))

    assert crowded < alone


@pytest.mark.e2e
def test_the_ledger_lives_in_the_database_it_ran_on(
    tmp_path: Path, backtest: Callable[..., str], signal_db: Path
) -> None:
    database = tmp_path / "ledger.sqlite"
    shutil.copy(signal_db, database)
    backtest(signal_db, keep=database)

    with Store.open(database, read_only=True) as store:
        assert len(store.trial_sharpes()) == 3
    with Store.open(signal_db, read_only=True) as untouched:
        assert untouched.trial_sharpes() == []


def test_a_negative_cost_is_refused(
    backtest: Callable[..., str], signal_db: Path
) -> None:
    with pytest.raises(SystemExit, match=r"cost is -0\.0001; it cannot be negative"):
        backtest(signal_db, "--cost-bps", "-1")


@pytest.mark.e2e
def test_the_alpha_table_asks_for_the_factors_until_they_are_stored(
    tmp_path: Path, backtest: Callable[..., str], signal_db: Path
) -> None:
    database = tmp_path / "with-factors.sqlite"
    shutil.copy(signal_db, database)

    before = backtest(signal_db, keep=database)
    main(["factors", "--source", "synthetic", "--db", str(database)])
    after = backtest(signal_db, keep=database)

    assert "Run `eq factors`." in before
    assert "synthetic factors" in after
    alpha = _section(after, "Spread alpha on the Fama-French five factors")
    assert [row.split()[0] for row in alpha[1:]] == ["LightGBM", "ridge", "composite"]
    assert "Run `eq factors`" not in after


def _french_archive() -> bytes:
    rows = "".join(
        f"{day:%Y%m%d},0.10,0.02,-0.01,0.03,0.00,0.01\r\n"
        for day in pd.bdate_range("2024-01-01", periods=30)
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "F-F_Research_Data_5_Factors_2x3_daily.csv",
            f"notes\r\n\r\n,Mkt-RF,SMB,HML,RMW,CMA,RF\r\n{rows}\r\n Copyright\r\n",
        )
    return buffer.getvalue()


@pytest.mark.e2e
def test_factors_that_cover_too_few_months_say_so_without_asking_for_more(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backtest: Callable[..., str],
    signal_db: Path,
) -> None:
    transport = httpx2.MockTransport(
        lambda _: httpx2.Response(200, content=_french_archive())
    )
    monkeypatch.setitem(
        cli.FACTORS, "french", lambda _: FamaFrenchSource(transport=transport)
    )
    database = tmp_path / "short-factors.sqlite"
    shutil.copy(signal_db, database)
    main(["factors", "--db", str(database)])

    output = backtest(signal_db, keep=database)

    alpha = _section(output, "Spread alpha on the Fama-French five factors")
    assert alpha == [
        "not computed: the factor file covers too few of the holding months."
    ]


def test_factors_stores_the_french_file_and_says_what_it_holds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transport = httpx2.MockTransport(
        lambda _: httpx2.Response(200, content=_french_archive())
    )
    monkeypatch.setitem(
        cli.FACTORS, "french", lambda _: FamaFrenchSource(transport=transport)
    )
    database = tmp_path / "db.sqlite"

    main(["factors", "--db", str(database)])

    assert (
        capsys.readouterr().out
        == f"stored 30 days of french factors, 2024-01-01 to 2024-02-09, in {database}\n"
    )
    with Store.open(database, read_only=True) as store:
        factors = store.read_factors()
        assert store.factors_source() == "french"
    assert factors["mkt_rf"].iloc[0] == pytest.approx(0.001)


def test_factors_from_two_sources_do_not_share_a_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = httpx2.MockTransport(
        lambda _: httpx2.Response(200, content=_french_archive())
    )
    monkeypatch.setitem(
        cli.FACTORS, "french", lambda _: FamaFrenchSource(transport=transport)
    )
    database = tmp_path / "db.sqlite"
    main(["factors", "--source", "synthetic", "--db", str(database)])

    with pytest.raises(SystemExit, match="holds synthetic factors"):
        main(["factors", "--source", "french", "--db", str(database)])
