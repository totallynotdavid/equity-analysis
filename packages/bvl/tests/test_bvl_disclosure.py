import numpy as np
import pandas as pd
import pytest

from bvl import disclosure, panel


DAYS = 600


def grid(log_price: np.ndarray, traded: np.ndarray | None = None) -> disclosure.Grid:
    """One-or-more-stock grid on a flat market, every day traded by default."""
    if traded is None:
        traded = np.ones_like(log_price, dtype=bool)
    return disclosure.Grid(
        days=pd.bdate_range("2010-01-01", periods=len(log_price)),
        tickers=pd.Index([f"S{i}" for i in range(log_price.shape[1])]),
        log_price=log_price,
        amount=np.where(traded, 1.0, 0.0),
        traded=traded,
        log_market=np.zeros(len(log_price)),
    )


def test_measure_puts_a_day_zero_jump_in_the_event_window_only() -> None:
    day0 = 400
    log_price = np.zeros((DAYS, 1))
    log_price[day0:] = 0.1
    measures = disclosure.measure(grid(log_price), np.array([0]), np.array([day0]))

    row = measures.iloc[0]
    assert row["car_event"] == pytest.approx(0.1)
    assert row["car_pre5"] == pytest.approx(0.0)
    assert row["car_post"] == pytest.approx(0.0)
    assert row["volume_event"] == pytest.approx(1.0)


def test_measure_rejects_a_stock_that_stopped_trading_before_the_event() -> None:
    day0 = 400
    traded = np.zeros((DAYS, 1), dtype=bool)
    traded[day0 - 5 :] = True
    measures = disclosure.measure(
        grid(np.zeros((DAYS, 1)), traded), np.array([0]), np.array([day0])
    )

    assert measures.iloc[0].isna().all()


def test_placebo_days_keep_away_from_filings_and_near_their_event() -> None:
    g = grid(np.zeros((DAYS, 1)))
    filings = np.array([300, 330, 500])
    events = pd.DataFrame({"ticker": ["S0"], "day0": [330]})
    placebos = disclosure.placebo_days(
        g, events, {"S0": filings}, np.random.default_rng(1)
    )

    days = placebos["day0"].to_numpy()
    assert len(days) == disclosure.PLACEBOS_PER_EVENT
    distance = np.abs(days[:, None] - filings[None, :]).min(axis=1)
    assert (distance > disclosure.QUIET).all()
    assert (np.abs(days - 330) <= disclosure.PLACEBO_RANGE).all()
    assert (days >= -disclosure.BASELINE[0]).all()


@pytest.mark.parametrize("statistic", ["mean", "median"])
def test_excess_finds_a_planted_effect_and_no_effect_where_none_is(
    statistic: str,
) -> None:
    rng = np.random.default_rng(3)
    events = pd.RangeIndex(400)
    clusters = pd.Series(np.arange(400) // 4, index=events)
    placebo = pd.Series(
        rng.normal(0, 1, 400 * 5), index=np.repeat(events.to_numpy(), 5)
    )
    noise = pd.Series(rng.normal(0, 1, 400), index=events)

    planted = disclosure.excess(noise + 0.5, placebo, clusters, statistic, rng, 300)
    null = disclosure.excess(noise, placebo, clusters, statistic, rng, 300)

    assert planted[1] > 0.2
    assert planted[0] == pytest.approx(0.5, abs=0.2)
    assert null[1] < 0 < null[2]


def test_market_level_skips_a_return_that_spans_a_missed_market_day() -> None:
    d = pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-03"])
    quotes = pd.DataFrame(
        {
            "date": [d[0], d[1], d[2], d[2]],
            "prev_date": [pd.NaT, d[0], d[1], d[0]],
            "log_return": [np.nan, 0.01, 0.02, 0.5],
        }
    )

    level = panel.market_log_level(quotes)

    assert level.to_numpy() == pytest.approx([0.0, 0.01, 0.03])
