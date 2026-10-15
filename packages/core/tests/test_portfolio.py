import numpy as np
import pandas as pd
import pytest

from index_core.portfolio import MIN_PERIODS, monthly_holdings, strategy


NAMES = [f"A{i:02d}" for i in range(20)]
# Twenty names make a decile two stocks wide: A18 and A19 on top, A00 and A01
# at the bottom, when the score is the name's number.
TOP, BOTTOM = ["A18", "A19"], ["A00", "A01"]
MONTHS = 8
CALENDAR = pd.bdate_range("2020-01-01", "2020-12-31", name="date")


def _signal_dates() -> pd.DatetimeIndex:
    firsts = (
        pd.Series(CALENDAR, index=CALENDAR).groupby(CALENDAR.to_period("M")).first()
    )
    return pd.DatetimeIndex(firsts.iloc[:MONTHS])


def _scores(order: list[list[str]] | None = None) -> pd.Series:
    """One score per name and signal date. `order` ranks the names of a month,
    worst to best; the default is the name's number every month."""
    rows = {}
    for month, date in enumerate(_signal_dates()):
        ranking = order[month % len(order)] if order else NAMES
        for position, name in enumerate(ranking):
            rows[(date, name)] = float(position)
    return pd.Series(rows).rename_axis(["date", "ticker"])


def _prices(growth: dict[str, list[float]]) -> pd.DataFrame:
    """Adjusted closes that move only on the entry day of each month, by the
    stated growth of the month that begins there. Entries are the day after
    each signal."""
    entries = [
        CALENDAR[int(CALENDAR.searchsorted(date)) + 1] for date in _signal_dates()
    ]
    frame = pd.DataFrame(100.0, index=CALENDAR, columns=NAMES)
    for name in NAMES:
        level = 100.0
        for entry, factor in zip(entries, growth[name], strict=False):
            frame.loc[entry:, name] = level
            level *= 1 + factor
    return frame


def _growth(
    top: tuple[float, float], bottom: float, others: float
) -> dict[str, list[float]]:
    rates = dict.fromkeys(NAMES, others)
    rates["A18"], rates["A19"] = top
    rates["A00"] = rates["A01"] = bottom
    return {name: [rate] * MONTHS for name, rate in rates.items()}


def test_a_leg_and_the_reference_earn_the_mean_of_their_stocks() -> None:
    holdings = monthly_holdings(
        _scores(), _prices(_growth(top=(0.10, 0.0), bottom=-0.05, others=0.01))
    )

    assert holdings.periods == MONTHS - 1
    assert holdings.top == pytest.approx(0.05)
    assert holdings.bottom == pytest.approx(-0.05)
    assert holdings.universe == pytest.approx((0.10 + 0.0 - 0.10 + 16 * 0.01) / 20)


def test_the_first_month_builds_each_leg_from_cash_and_drift_is_traded_back() -> None:
    holdings = monthly_holdings(
        _scores(), _prices(_growth(top=(0.10, 0.0), bottom=-0.05, others=0.01))
    )

    # A18 grew 10% and A19 did not, so the legs sit at 1.1/2.1 and 1/2.1 and
    # rebalancing sells and buys back the same 0.5 - 1.1/2.1 twice over.
    drift = 2 * abs(0.5 - 1.1 / 2.1)
    assert holdings.top_traded == pytest.approx([1.0, *[drift] * (MONTHS - 2)])
    # The bottom pair moved together, so nothing drifts.
    assert holdings.bottom_traded == pytest.approx([1.0, *[0.0] * (MONTHS - 2)])


def test_replacing_a_whole_leg_trades_twice_its_capital() -> None:
    first = NAMES
    flipped = NAMES[::-1]
    holdings = monthly_holdings(
        _scores([first, flipped]),
        _prices(_growth(top=(0.0, 0.0), bottom=0.0, others=0.0)),
    )

    assert holdings.top_traded == pytest.approx([1.0] + [2.0] * (MONTHS - 2))
    assert holdings.bottom_traded == pytest.approx([1.0] + [2.0] * (MONTHS - 2))


def test_a_stock_whose_prices_end_keeps_its_last_close_in_the_leg() -> None:
    prices = _prices(_growth(top=(0.10, 0.20), bottom=0.0, others=0.0))
    ended = _signal_dates()[3]
    prices.loc[prices.index > ended, "A19"] = np.nan

    holdings = monthly_holdings(_scores(), prices)

    assert holdings.periods == MONTHS - 1
    assert holdings.top[0] == pytest.approx(0.15)
    # After the last price A19 earns nothing but still holds half the leg.
    assert holdings.top[-1] == pytest.approx(0.05)


def test_too_few_months_are_refused() -> None:
    scores = _scores()
    few = scores[
        scores.index.get_level_values("date") < _signal_dates()[MIN_PERIODS - 1]
    ]

    with pytest.raises(ValueError, match="too few"):
        monthly_holdings(few, _prices(_growth(top=(0.0, 0.0), bottom=0.0, others=0.0)))


def _series(periods: int = 36, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    return (
        0.01 + 0.02 * rng.standard_normal(periods),
        rng.uniform(0.5, 1.5, periods),
    )


def test_the_net_return_falls_with_every_extra_basis_point() -> None:
    gross, traded = _series()

    nets = [strategy(gross, traded, bps / 1e4).net.value for bps in (0, 5, 10, 20, 50)]

    assert nets == sorted(nets, reverse=True)
    assert len(set(nets)) == len(nets)


def test_net_at_a_cost_agrees_with_a_strategy_built_at_that_cost() -> None:
    gross, traded = _series()
    base = strategy(gross, traded, 0.0)

    for bps in (5, 20, 50):
        assert base.net_at(bps / 1e4) == pytest.approx(
            strategy(gross, traded, bps / 1e4).net.value
        )
    assert base.net_at(0.0) == pytest.approx(base.gross.value)


def test_the_break_even_cost_zeroes_the_net_return() -> None:
    gross, traded = _series()
    base = strategy(gross, traded, 0.0)
    assert base.break_even is not None

    at_break_even = strategy(gross, traded, base.break_even.value)

    assert base.break_even.value == pytest.approx(gross.mean() / traded.mean())
    assert at_break_even.net.value == pytest.approx(0.0, abs=1e-12)
    assert base.break_even.low < base.break_even.value < base.break_even.high


def test_a_strategy_with_no_gross_edge_has_no_break_even_cost() -> None:
    gross, traded = _series()

    assert strategy(-np.abs(gross), traded, 0.001).break_even is None


def test_the_intervals_are_repeatable_and_widen_with_noise() -> None:
    gross, traded = _series()

    first, second = strategy(gross, traded, 0.001), strategy(gross, traded, 0.001)
    noisy = strategy(gross * 3 - 2 * gross.mean(), traded, 0.001)

    assert first.net == second.net
    assert first.break_even == second.break_even
    assert noisy.net.high - noisy.net.low > first.net.high - first.net.low


def test_turnover_is_the_mean_traded() -> None:
    gross, traded = _series()

    assert strategy(gross, traded, 0.001).turnover == pytest.approx(traded.mean())
