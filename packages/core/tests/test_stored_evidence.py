"""The proof page's data, from a backtest stored and read back.

The prices carry a planted signal, so the stored figures have known signs.
"""

import shutil

from datetime import date
from typing import TYPE_CHECKING

import pandas as pd
import pytest

from index_core.evidence import build_evidence
from index_core.feature_ic import HURDLE_T
from index_core.pipeline import Backtest, backtest
from index_core.sources.base import PRICE_COLUMNS
from index_core.store import Store
from index_core.walkforward import MODELS
from membership import always


if TYPE_CHECKING:
    from pathlib import Path

    from index_core.report import EvidenceReport

TICKERS = [f"T{i:02d}" for i in range(14)]
UNIVERSE = always(TICKERS, "planted")
FIRST_OOS = date(2019, 1, 1)
HOLDOUT = 12


@pytest.fixture(scope="module")
def prices_db(tmp_path_factory: pytest.TempPathFactory, planted: pd.DataFrame) -> Path:
    path = tmp_path_factory.mktemp("evidence") / "prices.sqlite"
    with Store.open(path) as opened:
        for ticker, bars in planted[planted["ticker"].isin(["SPY", *TICKERS])].groupby(
            "ticker"
        ):
            opened.upsert_prices(
                "synthetic", str(ticker), bars.set_index("date")[list(PRICE_COLUMNS)]
            )
    return path


@pytest.fixture(scope="module")
def held_out(
    prices_db: Path, tmp_path_factory: pytest.TempPathFactory
) -> tuple[Path, Backtest]:
    """A database after `backtest(final=True)`, and the result. Tests read it;
    those that change a database copy it."""
    path = tmp_path_factory.mktemp("held-out") / "db.sqlite"
    shutil.copy(prices_db, path)
    with Store.open(path) as store:
        return path, backtest(store, UNIVERSE, FIRST_OOS, HOLDOUT, final=True)


@pytest.fixture(scope="module")
def developed(prices_db: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("developed") / "db.sqlite"
    shutil.copy(prices_db, path)
    with Store.open(path) as store:
        backtest(store, UNIVERSE, FIRST_OOS, HOLDOUT)
    return path


def _evidence(path: Path) -> EvidenceReport:
    with Store.open(path, read_only=True) as store:
        report = build_evidence(store, UNIVERSE.name)
    assert report is not None
    return report


def _copy(path: Path, tmp_path: Path) -> Path:
    target = tmp_path / "copy.sqlite"
    shutil.copy(path, target)
    return target


def test_a_store_without_a_backtest_has_no_evidence(tmp_path: Path) -> None:
    with Store.open(tmp_path / "empty.sqlite") as empty:
        assert build_evidence(empty, "planted") is None


def test_the_evidence_is_what_the_backtest_measured(
    held_out: tuple[Path, Backtest],
) -> None:
    path, result = held_out

    report = _evidence(path)

    assert report.run.period == "holdout"
    assert report.verdict.decided
    assert report.verdict.candidate == result.verdict.candidate
    assert report.verdict.shown == result.verdict.show_score
    assert [c.passed for c in report.verdict.checks] == [
        c.passed for c in result.verdict.checks
    ]
    assert report.run.rows == result.evaluation.rows
    assert report.run.independent_windows == result.evaluation.independent_windows
    assert report.run.cost_bps == pytest.approx(result.cost * 1e4)
    assert [m.model for m in report.models] == list(MODELS)
    for shown in report.models:
        measured = result.models[shown.model]
        assert shown.mean_ic.value == pytest.approx(measured.evaluation.mean_ic.value)
        assert shown.mean_ic.lo == pytest.approx(measured.evaluation.mean_ic.low)
        assert shown.spread.net.value == pytest.approx(measured.spread.net.value)
        assert shown.spread.turnover == pytest.approx(measured.spread.turnover)
        assert shown.deflated.probability == pytest.approx(
            measured.deflated.probability
        )
        assert shown.deflated.trials == measured.deflated.trials
        assert (shown.spread.break_even is None) == (measured.spread.break_even is None)
    assert [b.score for b in report.by_score] == [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
    assert {f.feature for f in report.features} == {f.feature for f in result.features}
    assert report.feature_hurdle_t == HURDLE_T


def test_the_cost_ladder_falls_as_the_cost_rises(
    held_out: tuple[Path, Backtest],
) -> None:
    for model in _evidence(held_out[0]).models:
        costs = [step.cost_bps for step in model.ladder]
        net = [step.net for step in model.ladder]
        assert costs == sorted(costs)
        assert len(set(costs)) == len(costs) > 1
        assert net == sorted(net, reverse=True)


def test_the_years_cover_the_stored_rows_and_bracket_the_overall_ic(
    held_out: tuple[Path, Backtest],
) -> None:
    path, _ = held_out
    report = _evidence(path)

    with Store.open(path, read_only=True) as store:
        stored = store.latest_backtest(UNIVERSE.name)
    assert stored is not None
    assert sum(year.dates for year in report.by_year) == report.run.dates
    dates = pd.DatetimeIndex(stored.oos.index.get_level_values("date"))
    assert [year.year for year in report.by_year] == sorted(set(dates.year))
    # The overall mean is the mean over dates, so it lies between the extremes
    # of the yearly means.
    for model in report.models:
        yearly = [
            v
            for v in (y.ic[model.model].value for y in report.by_year)
            if v is not None
        ]
        assert model.mean_ic.value is not None
        assert min(yearly) <= model.mean_ic.value <= max(yearly)


def test_a_year_of_fewer_than_three_months_has_no_interval(developed: Path) -> None:
    years = _evidence(developed).by_year

    assert years
    for year in years:
        interval = year.ic["lightgbm"]
        assert (interval.lo is None) == (year.months < 3)
        assert interval.value is not None


def test_development_months_are_not_a_verdict(developed: Path) -> None:
    verdict = _evidence(developed).verdict

    assert not verdict.decided
    assert not verdict.shown


@pytest.mark.e2e
def test_a_later_backtest_replaces_the_earlier_one_of_its_period(
    developed: Path, tmp_path: Path
) -> None:
    before = _evidence(developed)
    path = _copy(developed, tmp_path)
    with Store.open(path) as store:
        backtest(store, UNIVERSE, FIRST_OOS, HOLDOUT + 6)

    after = _evidence(path)

    assert after.run.period == before.run.period == "development"
    assert after.run.holdout_start != before.run.holdout_start


@pytest.mark.e2e
def test_the_held_out_run_is_the_evidence_when_both_exist(
    held_out: tuple[Path, Backtest], developed: Path, tmp_path: Path
) -> None:
    path = _copy(held_out[0], tmp_path)
    with Store.open(path) as store:
        backtest(store, UNIVERSE, FIRST_OOS, HOLDOUT)
        stored = store.latest_backtest(UNIVERSE.name)

    assert stored is not None
    assert stored.run.period == "holdout"
    assert _evidence(path).run.period == "holdout"


def test_the_ledger_lists_every_specification_tried(
    held_out: tuple[Path, Backtest],
) -> None:
    path, _ = held_out
    report = _evidence(path)

    with Store.open(path, read_only=True) as store:
        assert len(report.trials) == len(store.trial_sharpes())
    assert all("planted" in trial.description for trial in report.trials)
    assert report.models[0].deflated.trials == len(report.trials)
