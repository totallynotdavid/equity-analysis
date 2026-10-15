import numpy as np
import pandas as pd
import pytest

from index_core.portfolio import Holdings
from index_core.significance import MIN_ALPHA_MONTHS, deflated_sharpe, factor_alpha
from index_core.sources.base import FACTOR_COLUMNS


def _returns(months: int = 60, mean: float = 0.01, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return mean + 0.03 * rng.standard_normal(months)


def test_with_a_single_trial_nothing_is_deflated() -> None:
    returns = _returns()
    sharpe = returns.mean() / returns.std(ddof=1)

    result = deflated_sharpe(returns, [sharpe])

    assert result.trials == 1
    assert result.benchmark == 0.0
    assert result.sharpe == pytest.approx(sharpe)
    assert result.probability > 0.95


def test_a_strategy_with_no_mean_return_is_a_coin_flip_before_deflation() -> None:
    returns = _returns(mean=0.0)
    returns -= returns.mean()

    result = deflated_sharpe(returns, [0.0])

    assert result.probability == pytest.approx(0.5)


def test_more_trials_raise_the_chance_sharpe_and_lower_the_probability() -> None:
    returns = _returns(mean=0.008)
    sharpe = returns.mean() / returns.std(ddof=1)
    rng = np.random.default_rng(1)
    others = list(0.1 * rng.standard_normal(200))

    few = deflated_sharpe(returns, [sharpe, *others[:4]])
    many = deflated_sharpe(returns, [sharpe, *others])

    assert many.trials == 201
    assert many.benchmark > few.benchmark > 0
    assert many.probability < few.probability


def test_trials_that_agree_leave_little_to_deflate() -> None:
    returns = _returns(mean=0.008)
    sharpe = returns.mean() / returns.std(ddof=1)

    tight = deflated_sharpe(returns, [sharpe + 1e-4 * k for k in range(50)])
    wide = deflated_sharpe(returns, [sharpe + 0.1 * k for k in range(50)])

    assert tight.benchmark < 0.01
    assert tight.probability > wide.probability


def test_the_benchmark_of_two_trials_matches_the_published_expected_maximum() -> None:
    returns = _returns()

    result = deflated_sharpe(returns, [0.0, 0.2])

    # Bailey and Lopez de Prado: sd * ((1 - g) * z(1 - 1/N) + g * z(1 - 1/(N e))).
    # With N = 2 the first normal quantile is 0 and the second is 0.9005 (to
    # four places), g = 0.5772, and the standard deviation of {0, 0.2} is 0.1414.
    assert result.benchmark == pytest.approx(0.1414214 * 0.5772157 * 0.9005, abs=1e-4)


CALENDAR = pd.bdate_range("2018-01-01", "2023-12-29", name="date")
LOADINGS = {"mkt_rf": 0.9, "smb": -0.3, "hml": 0.4, "rmw": 0.2, "cma": -0.1}
ALPHA = 0.004


def _factors(seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    data = 0.01 * rng.standard_normal((len(CALENDAR), len(FACTOR_COLUMNS)))
    data[:, -1] = 0.00005
    return pd.DataFrame(data, index=CALENDAR, columns=list(FACTOR_COLUMNS))


def _holdings(months: int, start: int = 0) -> Holdings:
    entries = pd.DatetimeIndex(
        [CALENDAR[start + 21 * k] for k in range(months)], name="date"
    )
    exits = pd.DatetimeIndex([CALENDAR[start + 21 * (k + 1)] for k in range(months)])
    zeros = np.zeros(months)
    return Holdings(
        entries, exits, np.full(months, 21), zeros, zeros, zeros, zeros, zeros
    )


def _compound(returns: pd.Series) -> float:
    return float(np.prod(1 + returns.to_numpy()) - 1)


def _planted(factors: pd.DataFrame, holdings: Holdings, noise: float) -> np.ndarray:
    rng = np.random.default_rng(5)
    returns = np.empty(holdings.periods)
    for k, (entry, exit_) in enumerate(zip(holdings.entry, holdings.exit, strict=True)):
        window = factors[(factors.index > entry) & (factors.index <= exit_)]
        market = _compound(window["mkt_rf"] + window["rf"]) - _compound(window["rf"])
        exposures = {"mkt_rf": market} | {
            name: _compound(window[name]) for name in list(LOADINGS)[1:]
        }
        returns[k] = (
            ALPHA
            + sum(LOADINGS[name] * value for name, value in exposures.items())
            + noise * rng.standard_normal()
        )
    return returns


def test_the_alpha_and_loadings_of_a_planted_strategy_are_recovered() -> None:
    factors = _factors()
    holdings = _holdings(months=60)
    returns = _planted(factors, holdings, noise=0.001)

    alpha = factor_alpha(returns, holdings, factors)

    assert alpha is not None
    assert alpha.months == 60
    assert alpha.monthly == pytest.approx(ALPHA, abs=0.0015)
    assert alpha.annual == pytest.approx(12 * alpha.monthly)
    assert alpha.t_stat > 3
    assert alpha.r2 > 0.9
    for name, loading in LOADINGS.items():
        assert alpha.loadings[name] == pytest.approx(loading, abs=0.08)


def test_a_strategy_with_no_alpha_has_an_insignificant_one() -> None:
    factors = _factors()
    holdings = _holdings(months=60)
    returns = _planted(factors, holdings, noise=0.01) - ALPHA

    alpha = factor_alpha(returns, holdings, factors)

    assert alpha is not None
    assert abs(alpha.t_stat) < 3


def test_months_the_factor_file_does_not_cover_are_left_out() -> None:
    factors = _factors()
    holdings = _holdings(months=60)
    returns = _planted(factors, holdings, noise=0.001)
    trailing = factors[factors.index < holdings.exit[-5]]

    alpha = factor_alpha(returns, holdings, trailing)

    assert alpha is not None
    assert alpha.months == 55


def test_too_few_covered_months_give_no_alpha() -> None:
    factors = _factors()
    holdings = _holdings(months=60)
    returns = _planted(factors, holdings, noise=0.001)
    short = factors[factors.index < holdings.exit[MIN_ALPHA_MONTHS - 2]]

    assert factor_alpha(returns, holdings, short) is None
