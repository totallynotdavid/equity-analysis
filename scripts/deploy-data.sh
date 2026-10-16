#!/usr/bin/env bash
# Scores the demo universe on real prices and filings into deploy/scores.json.
# It writes the ticker reports to deploy/tickers for the Vercel build, and the
# stored backtest of the universe to deploy/evidence.json if there is one.
# The reports lose their valuation and risk, which are worked out from prices
# and may not be published.
# TIINGO_API_KEY and SEC_USER_AGENT are required.
# Extra arguments go to `eq run`, for example `--end 2026-10-10`.
set -euo pipefail

uv sync --all-packages --locked
uv run eq factors --db data/real.sqlite --cache data/cache
uv run eq run --universe universes/demo30.txt \
  --db data/real.sqlite --cache data/cache --out deploy/scores.json "$@"
uv run python - deploy/tickers <<'PY'
import json
import pathlib
import sys

for path in pathlib.Path(sys.argv[1]).glob("*.json"):
    report = json.loads(path.read_text())
    for field in ("valuation", "risk"):
        report.pop(field, None)
    path.write_text(json.dumps(report, separators=(",", ":")) + "\n")
PY
