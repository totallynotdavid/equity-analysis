"""Risk and factor exposure: the regressions against stocks built from known
loadings, the figures against hand calculation, the refusals, and the risk a
pipeline run stores in the ticker reports."""

import math

from datetime import date
from typing import TYPE_CHECKING, cast

import numpy as np
import pandas as pd
import pytest

from index_core.pipeline import (
    compute_metrics,
    ingest,
    ingest_factors,
    ingest_filings,
)
from index_core.regression import newey_west_lags, ols_hac
from index_core.report import Risk, RiskFit, TickerReport
from index_core.risk import FACTORS, TRADING_DAYS, WINDOW, assess, max_drawdown
from index_core.sources.base import FACTOR_COLUMNS, PRICE_COLUMNS, NotFoundError
from index_core.sources.synthetic import SyntheticFilings
from index_core.store import Store
from membership import always


if TYPE_CHECKING:
    from pathlib import Path

RF = 0.00008
LOADINGS = {"mkt_rf": 1.2, "smb": 0.5, "hml": -0.3, "rmw": 0.4, "cma": -0.2}
ALPHA = 0.0002
SCALE = {"mkt_rf": 0.010, "smb": 0.005, "hml": 0.005, "rmw": 0.004, "cma": 0.004}


def _factors(days: int, seed: int = 0, start: str = "2021-01-04") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    index = pd.bdate_range(start, periods=days, name="date")
    frame = pd.DataFrame(
        {name: rng.normal(0.0002, SCALE[name], days) for name in FACTORS}, index=index
    )
    frame["rf"] = RF
    return frame[list(FACTOR_COLUMNS)]


def _built(
    factors: pd.DataFrame,
    noise: np.ndarray | pd.Series[float] | float,
    loadings: dict[str, float] = LOADINGS,
) -> pd.Series[float]:
    """The daily return of a stock whose excess return is its alpha, its loadings
    times the factors, and `noise`."""
    systematic = sum(loadings[name] * factors[name] for name in loadings)
    return cast("pd.Series[float]", factors["rf"] + ALPHA + systematic + noise)


def _market(factors: pd.DataFrame) -> pd.Series[float]:
    return factors["rf"] + factors["mkt_rf"]


def _ok(risk: Risk) -> RiskFit:
    assert risk.fit is not None, risk.reason
    return risk.fit


def test_a_noise_free_stock_returns_its_loadings_and_alpha_exactly() -> None:
    factors = _factors(WINDOW + 40)
    stock = _built(factors, 0.0)

    fit = _ok(assess(stock, _market(factors), factors))

    assert {item.factor: item.value for item in fit.loadings} == pytest.approx(LOADINGS)
    assert fit.alpha.value == pytest.approx(ALPHA * TRADING_DAYS)
    assert fit.r2 == pytest.approx(1.0)
    assert fit.beta.value == pytest.approx(LOADINGS["mkt_rf"], abs=0.2)


def test_intervals_of_noisy_stocks_cover_the_loadings_that_built_them() -> None:
    """The coefficients are inside their 95% intervals about 95% of the time,
    also where the noise has volatility that moves with the market and with
    its own past. Replaced by a plain least-squares error, the heteroskedastic
    case would fall well short."""
    simulations = 400
    covered = dict.fromkeys(["alpha", "beta", *LOADINGS], 0)
    for seed in range(simulations):
        rng = np.random.default_rng(1000 + seed)
        factors = _factors(WINDOW, seed=seed)
        # The noise is heteroskedastic and autocorrelated.
        sigma = 0.006 * (1 + 40 * factors["mkt_rf"].abs())
        shocks = rng.standard_normal(WINDOW) * sigma.to_numpy()
        noise = shocks + 0.2 * np.concatenate([[0.0], shocks[:-1]])
        fit = _ok(assess(_built(factors, noise), _market(factors), factors))

        for item in fit.loadings:
            covered[item.factor] += item.lo <= LOADINGS[item.factor] <= item.hi
        covered["alpha"] += fit.alpha.lo <= ALPHA * TRADING_DAYS <= fit.alpha.hi
        covered["beta"] += fit.beta.lo <= LOADINGS["mkt_rf"] <= fit.beta.hi

    for name, count in covered.items():
        assert 0.91 <= count / simulations <= 0.99, (name, count / simulations)


def test_a_wider_noise_gives_wider_intervals_and_a_lower_r2() -> None:
    factors = _factors(WINDOW)
    rng = np.random.default_rng(5)
    draws = rng.standard_normal(WINDOW)

    quiet = _ok(assess(_built(factors, 0.002 * draws), _market(factors), factors))
    loud = _ok(assess(_built(factors, 0.02 * draws), _market(factors), factors))

    assert loud.r2 < quiet.r2
    assert loud.beta.hi - loud.beta.lo > quiet.beta.hi - quiet.beta.lo
    for a, b in zip(quiet.loadings, loud.loadings, strict=True):
        assert b.hi - b.lo > a.hi - a.lo


def test_an_interval_is_the_estimate_plus_or_minus_1_96_standard_errors() -> None:
    factors = _factors(WINDOW)
    noise = np.random.default_rng(2).normal(0, 0.01, WINDOW)

    fit = _ok(assess(_built(factors, noise), _market(factors), factors))

    for item in [fit.beta, fit.alpha, *fit.loadings]:
        assert item.hi - item.value == pytest.approx(1.959964 * item.se, rel=1e-5)
        assert item.value - item.lo == pytest.approx(1.959964 * item.se, rel=1e-5)
        assert item.se > 0


def test_with_no_lags_the_error_is_the_white_one_and_exceeds_the_plain_one() -> None:
    """With a variance that grows with the regressor, the error of the slope is
    `sqrt(sum x^2 e^2) / sum x^2` on a centred regressor, times the
    `sqrt(n / (n - k))` scale, and larger than the error that assumes a constant
    variance."""
    rng = np.random.default_rng(11)
    x = rng.standard_normal(2000)
    y = 2 * x + rng.standard_normal(2000) * (0.2 + x**2)
    design = np.column_stack([np.ones(2000), x])
    centred = x - x.mean()

    fit = ols_hac(y, design, lags=0)

    residual = y - design @ fit.coefficients
    expected = (
        math.sqrt(np.sum(centred**2 * residual**2))
        / np.sum(centred**2)
        * math.sqrt(2000 / 1998)
    )
    plain = math.sqrt(np.sum(residual**2) / 1998 / np.sum(centred**2))
    assert fit.errors[1] == pytest.approx(expected, rel=1e-6)
    assert fit.errors[1] > 1.2 * plain


def test_the_window_is_the_last_days_with_both_a_return_and_a_factor() -> None:
    factors = _factors(WINDOW + 100)
    # The first 100 days have different loadings from the remaining days.
    old = _built(factors.iloc[:100], 0.0, {"mkt_rf": 3.0})
    recent = _built(factors.iloc[100:], 0.0)
    stock = pd.concat([old, recent])
    # Returns after the factor file ends are not used.
    later = pd.Series(
        0.5, index=pd.bdate_range(factors.index[-1] + pd.offsets.BDay(), periods=20)
    )

    risk = assess(
        pd.concat([stock, later]),
        pd.concat([_market(factors), later]),
        factors,
    )

    fit = _ok(risk)
    assert risk.window == WINDOW
    assert fit.end == factors.index[-1].date()
    assert fit.start == factors.index[-WINDOW].date()
    assert {item.factor: item.value for item in fit.loadings} == pytest.approx(LOADINGS)


def test_an_exact_copy_of_the_market_has_beta_one_and_r2_near_one() -> None:
    factors = _factors(WINDOW + 30, seed=3)
    rng = np.random.default_rng(8)
    # SPY tracks the market factor with small index-fund tracking noise.
    spy = _market(factors) + rng.normal(0, 0.0004, len(factors))

    fit = _ok(assess(spy.copy(), spy, factors))

    assert fit.beta.value == pytest.approx(1.0)
    assert fit.beta.se < 1e-9
    assert fit.r2 > 0.99
    market = next(item for item in fit.loadings if item.factor == "mkt_rf")
    assert market.value == pytest.approx(1.0, abs=0.02)
    assert all(
        abs(item.value) < 0.05 for item in fit.loadings if item.factor != "mkt_rf"
    )


def test_volatility_is_the_annualised_standard_deviation_of_daily_returns() -> None:
    factors = _factors(WINDOW)
    swing = pd.Series(
        np.where(np.arange(WINDOW) % 2 == 0, 0.01, -0.01), index=factors.index
    )

    fit = _ok(assess(swing, _market(factors), factors))

    daily = 0.01 * math.sqrt(WINDOW / (WINDOW - 1))
    assert fit.volatility == pytest.approx(daily * math.sqrt(TRADING_DAYS))


def test_the_maximum_drawdown_is_the_largest_fall_from_a_peak() -> None:
    # Both drawdowns use 1.1 as their peak. The later fall is the larger one.
    returns = np.array([0.10, -0.20, 0.05, -0.10, 0.50])

    assert max_drawdown(returns) == pytest.approx(0.8316 / 1.1 - 1)


def test_a_position_that_only_rises_has_no_drawdown() -> None:
    assert max_drawdown(np.array([0.01, 0.0, 0.02])) == 0.0


def test_a_drawdown_before_the_first_peak_counts_from_the_start() -> None:
    assert max_drawdown(np.array([-0.30, 0.10])) == pytest.approx(-0.30)


def test_the_window_needs_that_many_days_and_says_how_many_there_are() -> None:
    factors = _factors(WINDOW + 50)
    stock = _built(factors, 0.0).iloc[-(WINDOW - 1) :]

    risk = assess(stock, _market(factors), factors)

    assert risk.status == "insufficient_history"
    assert risk.fit is None
    assert risk.reason == (
        f"{WINDOW - 1} days have both a return and factor returns, "
        f"and the window is {WINDOW}"
    )
    assert risk.window == WINDOW


def test_one_day_more_makes_a_fit() -> None:
    factors = _factors(WINDOW + 50)

    risk = assess(_built(factors, 0.0).iloc[-WINDOW:], _market(factors), factors)

    assert risk.status == "ok"


def test_days_a_stock_lacks_are_not_counted_toward_the_window() -> None:
    factors = _factors(WINDOW + 10)
    stock = _built(factors, 0.0)
    stock.iloc[100:120] = np.nan

    risk = assess(stock, _market(factors), factors)

    assert risk.status == "insufficient_history"
    assert risk.reason is not None
    assert risk.reason.startswith(f"{WINDOW + 10 - 20} days")


def test_without_the_factor_file_there_is_nothing_to_regress_on() -> None:
    factors = _factors(WINDOW)

    risk = assess(_built(factors, 0.0), _market(factors), factors.iloc[:0])

    assert risk.status == "insufficient_history"
    assert risk.reason is not None
    assert "eq factors" in risk.reason


def test_a_stock_that_never_moves_cannot_be_estimated() -> None:
    factors = _factors(WINDOW)
    flat = pd.Series(RF, index=factors.index)

    risk = assess(flat, _market(factors), factors)

    assert risk.status == "not_estimable"
    assert risk.reason == "the stock's return does not vary"


def test_factors_that_are_collinear_cannot_be_estimated() -> None:
    factors = _factors(WINDOW)
    factors["smb"] = 0.0

    risk = assess(_built(factors, 0.001), _market(factors), factors)

    assert risk.status == "not_estimable"
    assert risk.reason == "the market or factor returns are collinear"


def test_a_risk_has_a_fit_or_a_reason_and_never_both() -> None:
    factors = _factors(WINDOW)
    fit = _ok(assess(_built(factors, 0.001), _market(factors), factors))

    with pytest.raises(ValueError, match="ok risk"):
        Risk(status="ok", window=WINDOW)
    with pytest.raises(ValueError, match="ok risk"):
        Risk(status="insufficient_history", reason="why", window=WINDOW, fit=fit)
    with pytest.raises(ValueError, match="ok risk"):
        Risk(status="not_estimable", window=WINDOW)


def test_the_bandwidth_follows_the_newey_west_rule() -> None:
    assert newey_west_lags(756) == 6
    assert newey_west_lags(100) == 4
    assert newey_west_lags(2000) == 7


class _FakeFactors:
    name = "french"

    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame

    def fetch(self) -> pd.DataFrame:
        return self._frame


class _Bars:
    """A price source with a given daily return per ticker."""

    name = "synthetic"

    def __init__(self, returns: dict[str, pd.Series[float]]) -> None:
        self._returns = returns

    def fetch(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        if ticker not in self._returns:
            raise NotFoundError(ticker)
        returns = self._returns[ticker]
        close = 100 * (1 + returns).cumprod()
        bars = pd.DataFrame(
            dict.fromkeys(PRICE_COLUMNS, 1.0) | {"close": close, "adj_close": close},
            index=close.index,
        )
        return bars.loc[pd.Timestamp(start) : pd.Timestamp(end)]


# The prices run 60 days past the end of the factor file, as they do in life.
DAYS, LAG = WINDOW + 150, 60
TICKERS = ["KNOWN", "COPY", "SHORT", "NOPRICE"]


def _run(
    path: Path, *, stored_factors: bool = True
) -> tuple[dict[str, TickerReport], pd.DataFrame]:
    factors = _factors(DAYS)
    rng = np.random.default_rng(21)
    noise = pd.Series(rng.normal(0, 0.008, DAYS), index=factors.index)
    spy = _market(factors) + rng.normal(0, 0.0004, DAYS)
    returns = {
        "SPY": spy,
        "KNOWN": _built(factors, noise),
        "COPY": spy,
        "SHORT": _built(factors, noise).iloc[-300:],
    }
    # SPY and the stocks go on after the last day of the factor file.
    with Store.open(path) as store:
        if stored_factors:
            ingest_factors(_FakeFactors(factors.iloc[:-LAG]), store)
        end = factors.index[-1].date()
        ingest(_Bars(returns), store, TICKERS, factors.index[0].date(), end)
        ingest_filings(SyntheticFilings(), store, TICKERS, end)
        compute_metrics(store, always(TICKERS, "demo"))
        return (
            {r.ticker: r for r in store.ticker_reports("demo")},
            factors.iloc[:-LAG],
        )


def test_a_pipeline_run_gives_each_report_its_risk(tmp_path: Path) -> None:
    reports, _ = _run(tmp_path / "db.sqlite")

    assert sorted(reports) == sorted(TICKERS)
    assert [reports[t].risk is not None for t in TICKERS] == [True, True, True, False]
    assert reports["KNOWN"].risk is not None
    assert reports["KNOWN"].risk.status == "ok"


def test_a_stock_built_from_known_loadings_recovers_them_in_a_run(
    tmp_path: Path,
) -> None:
    reports, _ = _run(tmp_path / "db.sqlite")

    risk = reports["KNOWN"].risk
    assert risk is not None
    fit = _ok(risk)
    for item in fit.loadings:
        assert item.lo <= LOADINGS[item.factor] <= item.hi, item
    assert fit.alpha.lo <= ALPHA * TRADING_DAYS <= fit.alpha.hi
    assert fit.beta.lo <= LOADINGS["mkt_rf"] <= fit.beta.hi
    assert 0.5 < fit.r2 < 0.95


def test_the_window_of_a_run_ends_with_the_factor_file(tmp_path: Path) -> None:
    reports, factors = _run(tmp_path / "db.sqlite")

    risk = reports["KNOWN"].risk
    assert risk is not None
    fit = _ok(risk)
    assert fit.end == factors.index[-1].date()
    assert fit.start == factors.index[-WINDOW].date()
    assert reports["KNOWN"].as_of > fit.end


def test_a_copy_of_spy_in_a_run_has_beta_one_and_r2_near_one(tmp_path: Path) -> None:
    reports, _ = _run(tmp_path / "db.sqlite")

    risk = reports["COPY"].risk
    assert risk is not None
    fit = _ok(risk)
    assert fit.beta.value == pytest.approx(1.0)
    assert fit.r2 > 0.99


def test_a_stock_with_too_little_history_says_so_in_a_run(tmp_path: Path) -> None:
    reports, _ = _run(tmp_path / "db.sqlite")

    risk = reports["SHORT"].risk
    assert risk is not None
    assert risk.status == "insufficient_history"
    assert risk.fit is None
    assert risk.reason is not None
    assert risk.reason.startswith("239 days have both")


def test_a_ticker_without_prices_has_no_risk(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite"
    _run(path)

    with Store.open(path, read_only=True) as store:
        assert store.read_risk("demo")["ticker"].tolist() == ["COPY", "KNOWN"]


def test_without_the_factor_file_every_report_says_to_run_eq_factors(
    tmp_path: Path,
) -> None:
    reports, _ = _run(tmp_path / "db.sqlite", stored_factors=False)

    for ticker in ("KNOWN", "COPY", "SHORT"):
        report = reports[ticker]
        assert report.risk is not None
        assert report.risk.status == "insufficient_history"
        assert report.risk.reason is not None
        assert "eq factors" in report.risk.reason


def test_the_exposures_table_holds_what_the_report_shows(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite"
    reports, _ = _run(path)

    with Store.open(path, read_only=True) as store:
        exposures = store.read_exposures("demo")
        summary = store.read_risk("demo").set_index("ticker")
        again = store.ticker_report("KNOWN")

    risk = reports["KNOWN"].risk
    assert risk is not None
    fit = _ok(risk)
    rows = exposures[exposures["ticker"] == "KNOWN"].set_index("factor")
    assert rows.index.tolist() == ["alpha", *FACTORS]
    assert rows.loc["alpha", "beta"] == pytest.approx(fit.alpha.value)
    for item in fit.loadings:
        assert rows.loc[item.factor, "beta"] == pytest.approx(item.value)
        assert rows.loc[item.factor, "se"] == pytest.approx(item.se)
    stored = summary.loc["KNOWN"]
    assert stored["window"] == WINDOW
    assert stored["r2"] == pytest.approx(fit.r2)
    assert stored["volatility"] == pytest.approx(fit.volatility)
    assert stored["max_drawdown"] == pytest.approx(fit.max_drawdown)
    assert stored["beta"] == pytest.approx(fit.beta.value)
    assert stored["start"] == fit.start.isoformat()
    assert stored["end"] == fit.end.isoformat()
    assert again == reports["KNOWN"]


def test_a_new_run_replaces_the_stored_exposures(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite"
    _run(path)
    with Store.open(path) as store:
        store.replace_risk("demo", date(2026, 1, 1), {})
        assert store.read_risk("demo").empty
        assert store.read_exposures("demo").empty
