"""Record real EDGAR `companyfacts` payloads as test fixtures.

    SEC_USER_AGENT='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36' \\
      uv run python packages/core/tests/record_companyfacts.py AAPL:320193 MSFT:789019

Each argument is `TICKER:CIK` and writes `recorded_TICKER_companyfacts.json`.
A payload is cut down to the tags, units and forms that `parse_companyfacts`
reads and to filings since `--since`, so the files stay small. Entries are kept
as EDGAR sent them.
"""

import argparse
import json
import os
import sys
import time

from pathlib import Path
from typing import Any

import httpx2

from index_core.sources.edgar import CONCEPTS, DATA_URL, FORMS, USER_AGENT_VARIABLE


FIXTURES = Path(__file__).parent / "fixtures"
PAUSE = 1.0


def trim(payload: dict[str, Any], since: str) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    for spec in CONCEPTS.values():
        source = payload["facts"].get(spec.taxonomy, {})
        for tag in spec.tags:
            entries = [
                entry
                for entry in source.get(tag, {}).get("units", {}).get(spec.unit, [])
                if entry.get("form") in FORMS and entry["filed"] >= since
            ]
            if entries:
                facts.setdefault(spec.taxonomy, {})[tag] = {
                    "units": {spec.unit: entries}
                }
    return {
        "cik": payload["cik"],
        "entityName": payload["entityName"],
        "facts": facts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("companies", nargs="+", metavar="TICKER:CIK")
    parser.add_argument("--since", default="2022-01-01", help="first filing date")
    args = parser.parse_args()

    user_agent = os.environ.get(USER_AGENT_VARIABLE)
    if not user_agent:
        raise SystemExit(f"{USER_AGENT_VARIABLE} is not set")
    for number, company in enumerate(args.companies):
        ticker, _, cik = company.partition(":")
        if number:
            time.sleep(PAUSE)
        response = httpx2.get(
            f"{DATA_URL}/api/xbrl/companyfacts/CIK{int(cik):010d}.json",
            headers={"User-Agent": user_agent},
            timeout=30.0,
        )
        response.raise_for_status()
        out = FIXTURES / f"recorded_{ticker.lower()}_companyfacts.json"
        out.write_text(json.dumps(trim(response.json(), args.since), indent=1) + "\n")
        sys.stdout.write(f"wrote {out} ({out.stat().st_size} bytes)\n")


if __name__ == "__main__":
    main()
