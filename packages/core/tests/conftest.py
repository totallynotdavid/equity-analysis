from datetime import date

import pandas as pd
import pytest

from index_core.sources.synthetic import SyntheticSource


@pytest.fixture
def prices() -> pd.DataFrame:
    """About 700 trading days for twelve tickers and the SPY benchmark."""
    source = SyntheticSource()
    frames = [
        source.fetch(ticker, date(2024, 1, 1), date(2026, 9, 30))
        .reset_index()
        .assign(ticker=ticker)
        for ticker in ["SPY", "AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
        + [f"T{i:02d}" for i in range(6)]
    ]
    return pd.concat(frames, ignore_index=True)
