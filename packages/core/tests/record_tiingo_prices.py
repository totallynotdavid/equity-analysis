"""Record a real Tiingo daily-prices response as a test fixture.

    TIINGO_API_KEY=... \\
      uv run python packages/core/tests/record_tiingo_prices.py \\
      AAPL 2020-07-31 2020-09-01

The response is kept as Tiingo sent it. The replay test in `test_sources.py`
picks up `recorded_aapl_prices.json` once it exists. It expects Apple's
dividend of 2020-08-07 and its 4-for-1 split of 2020-08-31 in the window, so
record exactly the dates above.
"""

import argparse
import json
import os
import sys

from datetime import date
from pathlib import Path

import httpx2

from index_core.sources.base import normalise_ticker
from index_core.sources.tiingo import BASE_URL, KEY_VARIABLE


FIXTURES = Path(__file__).parent / "fixtures"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("ticker")
    parser.add_argument("start", type=date.fromisoformat)
    parser.add_argument("end", type=date.fromisoformat)
    args = parser.parse_args()

    api_key = os.environ.get(KEY_VARIABLE)
    if not api_key:
        raise SystemExit(f"{KEY_VARIABLE} is not set")
    ticker = normalise_ticker(args.ticker)
    response = httpx2.get(
        f"{BASE_URL}/tiingo/daily/{ticker}/prices",
        params={
            "startDate": args.start.isoformat(),
            "endDate": args.end.isoformat(),
            "format": "json",
        },
        headers={"Authorization": f"Token {api_key}"},
        timeout=30.0,
    )
    response.raise_for_status()

    out = FIXTURES / f"recorded_{ticker.lower()}_prices.json"
    out.write_text(json.dumps(response.json(), indent=1) + "\n")
    sys.stdout.write(f"wrote {out} ({out.stat().st_size} bytes)\n")


if __name__ == "__main__":
    main()
