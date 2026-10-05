"""Reading a universe file: one ticker per line, `#` starts a comment."""

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from pathlib import Path

BENCHMARK = "SPY"


def read_universe(path: Path) -> list[str]:
    tickers: list[str] = []
    for line in path.read_text().splitlines():
        ticker = line.split("#", 1)[0].strip().upper()
        if not ticker:
            continue
        if ticker == BENCHMARK:
            raise ValueError(f"{BENCHMARK} is the benchmark and cannot be a member")
        if ticker in tickers:
            raise ValueError(f"{ticker} is listed twice in {path}")
        tickers.append(ticker)
    if not tickers:
        raise ValueError(f"{path} lists no tickers")
    return tickers
