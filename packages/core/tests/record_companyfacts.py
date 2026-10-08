"""Record a real EDGAR `companyfacts` payload as a test fixture.

    SEC_USER_AGENT="Jane Doe jane@example.com" \\
      uv run python packages/core/tests/record_companyfacts.py 320193 aapl

The payload is cut down to the tags, units and forms that `parse_companyfacts`
reads and to filings since `--since`, so the file stays small. Entries are kept
as EDGAR sent them.
"""

import argparse
import json
import os
import sys

from pathlib import Path
from typing import Any

import httpx2

from index_core.sources.edgar import CONCEPTS, DATA_URL, FORMS, USER_AGENT_VARIABLE


FIXTURES = Path(__file__).parent / "fixtures"


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
    parser.add_argument("cik", type=int)
    parser.add_argument("name", help="file name stem: recorded_NAME_companyfacts.json")
    parser.add_argument("--since", default="2022-01-01", help="first filing date")
    args = parser.parse_args()

    user_agent = os.environ.get(USER_AGENT_VARIABLE)
    if not user_agent:
        raise SystemExit(f"{USER_AGENT_VARIABLE} is not set")
    response = httpx2.get(
        f"{DATA_URL}/api/xbrl/companyfacts/CIK{args.cik:010d}.json",
        headers={"User-Agent": user_agent},
        timeout=30.0,
    )
    response.raise_for_status()

    out = FIXTURES / f"recorded_{args.name}_companyfacts.json"
    out.write_text(json.dumps(trim(response.json(), args.since), indent=1) + "\n")
    sys.stdout.write(f"wrote {out} ({out.stat().st_size} bytes)\n")


if __name__ == "__main__":
    main()
