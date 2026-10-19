#!/usr/bin/env bash
# Scores the demo universe on real prices and filings into deploy/scores.json.
# It writes the ticker reports to deploy/tickers for the Vercel build, and the
# stored backtest of the universe to deploy/evidence.json if there is one.
# The reports lose their valuation and risk, which are worked out from prices
# and may not be published.
# TIINGO_API_KEY is required. SEC_USER_AGENT comes from mise.toml.
# Extra arguments go to `eq run`, for example `--end 2026-10-10`.
set -euo pipefail

ledger_dir=${DEPLOY_LEDGER_DIR:-ledger}
deploy_dir=${DEPLOY_DIR:-deploy}
db=${DEPLOY_DB:-data/real.sqlite}
cache=${DEPLOY_CACHE:-data/cache}
universe=${DEPLOY_UNIVERSE:-universes/demo30.txt}
factors_source=${DEPLOY_FACTORS_SOURCE:-french}
staging_dir=$(mktemp -d "${TMPDIR:-.}/equity-analysis-deploy.XXXXXX")
trap 'rm -rf "$staging_dir"' EXIT

uv sync --all-packages --locked
if [ -d "$ledger_dir" ]; then
  uv run eq ledger upgrade --ledger "$ledger_dir"
  uv run eq audit "$ledger_dir" --status "$staging_dir/ledger-status.json"
fi
uv run eq factors --source "$factors_source" --db "$db" --cache "$cache"
uv run eq run --universe "$universe" \
  --db "$db" --cache "$cache" --out "$staging_dir/scores.json" \
  --tickers "$staging_dir/tickers" "$@"
uv run eq ledger publish \
  --scores "$staging_dir/scores.json" --ledger "$ledger_dir" --cache "$cache" \
  --status "$staging_dir/ledger-status.json"
uv run python - "$staging_dir/tickers" <<'PY'
import json
import pathlib
import sys

for path in pathlib.Path(sys.argv[1]).glob("*.json"):
    report = json.loads(path.read_text())
    for field in ("valuation", "risk"):
        report.pop(field, None)
    path.write_text(json.dumps(report, separators=(",", ":")) + "\n")
PY

mkdir -p "$deploy_dir"
mv "$staging_dir/scores.json" "$deploy_dir/scores.json"
rm -rf "$deploy_dir/tickers"
mv "$staging_dir/tickers" "$deploy_dir/tickers"
if [ -f "$staging_dir/evidence.json" ]; then
  mv "$staging_dir/evidence.json" "$deploy_dir/evidence.json"
fi
mv "$staging_dir/ledger-status.json" "$deploy_dir/ledger-status.json"
