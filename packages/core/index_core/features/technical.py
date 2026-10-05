"""Technical features from daily bars.

Every feature at date t reads bars up to and including t. Each is a ratio or an
oscillator, so values compare across stocks of any price level. Williams %R is
left out: it is `stoch_k - 100`.
"""

import numpy as np
import pandas as pd


FEATURES = (
    "ret_5",
    "ret_21",
    "ret_63",
    "ret_126",
    "mom_12_1",
    "dist_high_252",
    "dist_low_252",
    "vol_21",
    "vol_63",
    "beta_63",
    "rsi_14",
    "macd_hist",
    "bb_pctb",
    "bb_width",
    "atr_pct",
    "adx_14",
    "stoch_k",
    "volume_ratio",
    "log_dollar_volume",
    "max_drawdown_126",
)


def technical_features(prices: pd.DataFrame, benchmark: str) -> pd.DataFrame:
    """Features for every ticker except the benchmark.

    `prices` is the long frame from `Store.read_prices` and must contain the
    benchmark. The result is indexed by (`date`, `ticker`) over the benchmark's
    calendar, with the columns in `FEATURES`. A ticker's missing days stay NaN.
    """
    bars = {
        str(ticker): group.set_index("date").drop(columns="ticker")
        for ticker, group in prices.groupby("ticker")
    }
    calendar = bars[benchmark].index
    market = bars[benchmark]["adj_close"].pct_change()

    frames = {
        ticker: _ticker_features(bar.reindex(calendar), market)
        for ticker, bar in bars.items()
        if ticker != benchmark
    }
    features = pd.concat(frames, names=["ticker"]).swaplevel().sort_index()
    return features.replace([np.inf, -np.inf], np.nan)


def _ticker_features(bars: pd.DataFrame, market: pd.Series) -> pd.DataFrame:
    close = bars["adj_close"]
    high = bars["adj_high"]
    low = bars["adj_low"]
    volume = bars["adj_volume"]
    returns = close.pct_change()

    bollinger_mean = close.rolling(20).mean()
    bollinger_std = close.rolling(20).std()

    macd = _ema(close, 12) - _ema(close, 26)
    true_range = _true_range(high, low, close)
    atr = _wilder(true_range)

    lowest = low.rolling(14).min()
    highest = high.rolling(14).max()

    features = {
        "ret_5": close.pct_change(5),
        "ret_21": close.pct_change(21),
        "ret_63": close.pct_change(63),
        "ret_126": close.pct_change(126),
        "mom_12_1": close.shift(21) / close.shift(252) - 1,
        "dist_high_252": close / close.rolling(252).max() - 1,
        "dist_low_252": close / close.rolling(252).min() - 1,
        "vol_21": returns.rolling(21).std(),
        "vol_63": returns.rolling(63).std(),
        "beta_63": returns.rolling(63).cov(market) / market.rolling(63).var(),
        "rsi_14": _rsi(close),
        "macd_hist": (macd - _ema(macd, 9)) / close,
        "bb_pctb": (close - bollinger_mean + 2 * bollinger_std) / (4 * bollinger_std),
        "bb_width": 4 * bollinger_std / bollinger_mean,
        "atr_pct": atr / close,
        "adx_14": _adx(high, low, atr),
        "stoch_k": 100 * (close - lowest) / (highest - lowest),
        "volume_ratio": volume.rolling(21).mean() / volume.rolling(63).mean(),
        "log_dollar_volume": np.log(
            (bars["close"] * bars["volume"]).rolling(21).mean()
        ),
        "max_drawdown_126": close.rolling(126).apply(_max_drawdown, raw=True),
    }
    return pd.DataFrame(features)[list(FEATURES)]


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def _wilder(series: pd.Series, period: int = 14) -> pd.Series:
    return series.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _rsi(close: pd.Series) -> pd.Series:
    change = close.diff()
    gain = _wilder(change.clip(lower=0))
    loss = _wilder(-change.clip(upper=0))
    return 100 - 100 / (1 + gain / loss)


def _true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    previous = close.shift()
    ranges = pd.concat(
        [high - low, (high - previous).abs(), (low - previous).abs()], axis=1
    )
    return ranges.max(axis=1, skipna=False)


def _adx(high: pd.Series, low: pd.Series, atr: pd.Series) -> pd.Series:
    up = high.diff()
    down = -low.diff()
    plus = up.where((up > down) & (up > 0), 0.0).where(up.notna())
    minus = down.where((down > up) & (down > 0), 0.0).where(down.notna())
    plus_di = 100 * _wilder(plus) / atr
    minus_di = 100 * _wilder(minus) / atr
    return _wilder(100 * (plus_di - minus_di).abs() / (plus_di + minus_di))


def _max_drawdown(window: np.ndarray) -> float:
    return float((window / np.maximum.accumulate(window) - 1).min())
