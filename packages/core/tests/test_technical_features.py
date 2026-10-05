import numpy as np
import pandas as pd
import pytest

from index_core.features.normalize import rank_by_date
from index_core.features.technical import FEATURES, technical_features
from index_core.sources.base import PRICE_COLUMNS


def _ranked_features(prices: pd.DataFrame) -> pd.DataFrame:
    return rank_by_date(technical_features(prices, "SPY"))


def _up_to(frame: pd.DataFrame, day: pd.Timestamp) -> pd.DataFrame:
    return frame[frame.index.get_level_values("date") <= day]


def test_computes_twenty_features_for_every_ticker_but_the_benchmark(
    prices: pd.DataFrame,
) -> None:
    features = technical_features(prices, "SPY")

    assert len(FEATURES) == 20
    assert list(features.columns) == list(FEATURES)
    tickers = set(features.index.get_level_values("ticker"))
    assert tickers == set(prices["ticker"]) - {"SPY"}
    assert not np.isinf(features.to_numpy()).any()


def test_features_at_a_date_do_not_depend_on_later_data(
    prices: pd.DataFrame,
) -> None:
    calendar = pd.DatetimeIndex(sorted(prices["date"].unique()))
    day = calendar[330]
    after = prices["date"] > day

    garbage = prices.copy()
    garbage.loc[after, list(PRICE_COLUMNS)] = np.random.default_rng(0).uniform(
        0.01, 1000, (after.sum(), len(PRICE_COLUMNS))
    )
    truncated = prices[~after]

    full = _ranked_features(prices)
    expected = _up_to(full, day)
    pd.testing.assert_frame_equal(_up_to(_ranked_features(garbage), day), expected)
    pd.testing.assert_frame_equal(_ranked_features(truncated), expected)

    # The garbage must have reached the features, or the check above is vacuous.
    later = full.index.get_level_values("date") > day
    assert not _ranked_features(garbage)[later].equals(full[later])


def test_a_stock_moving_with_twice_the_market_has_beta_two(
    prices: pd.DataFrame,
) -> None:
    market = prices[prices["ticker"] == "SPY"].set_index("date")["adj_close"]
    leveraged = (1 + 2 * market.pct_change().fillna(0)).cumprod() * 100
    doubled = prices[prices["ticker"] == "AAA"].copy()
    doubled["adj_close"] = leveraged.to_numpy()
    others = prices[prices["ticker"] != "AAA"]

    features = technical_features(pd.concat([others, doubled]), "SPY")

    beta = features.xs("AAA", level="ticker")["beta_63"].dropna()
    assert beta.iloc[-1] == pytest.approx(2.0)


def test_returns_and_the_range_oscillators_match_hand_calculation(
    prices: pd.DataFrame,
) -> None:
    features = technical_features(prices, "SPY").xs("BBB", level="ticker")
    bars = prices[prices["ticker"] == "BBB"].set_index("date")
    last = bars.index[-1]
    close = bars["adj_close"]

    assert features.loc[last, "ret_21"] == pytest.approx(
        close.iloc[-1] / close.iloc[-22] - 1
    )
    assert features.loc[last, "dist_high_252"] == pytest.approx(
        close.iloc[-1] / close.iloc[-252:].max() - 1
    )
    window = bars.iloc[-14:]
    stochastic = (close.iloc[-1] - window["adj_low"].min()) / (
        window["adj_high"].max() - window["adj_low"].min()
    )
    assert features.loc[last, "stoch_k"] == pytest.approx(100 * stochastic)
    assert 0 <= features.loc[last, "rsi_14"] <= 100
    assert -1 <= features.loc[last, "max_drawdown_126"] <= 0


def test_ranking_puts_each_feature_on_zero_to_one_within_a_date(
    prices: pd.DataFrame,
) -> None:
    ranked = _ranked_features(prices).dropna()
    last = ranked.xs(ranked.index.get_level_values("date").max(), level="date")

    assert last["ret_21"].max() == 1.0
    assert last["ret_21"].min() == pytest.approx(1 / len(last))
