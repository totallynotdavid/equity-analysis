#!/usr/bin/env bash
# Loads the committed deploy/scores.json and deploy/tickers into
# data/deploy.sqlite for the API. It refuses files whose prices or filings are
# not real, ticker reports of another universe or as-of date than the scores
# or with a valuation or risk, and a deploy/evidence.json that is synthetic or
# of another universe.
set -euo pipefail

scores=deploy/scores.json
tickers=deploy/tickers

uv sync --all-packages --locked
uv run python - "$scores" "$tickers" <<'PY'
import json
import pathlib
import sys

path, tickers = sys.argv[1:]
scores = json.load(open(path))
sources = (scores["price_source"], scores["facts_source"])
if sources != ("tiingo", "edgar"):
    sys.exit(f"{path} has prices from {sources[0]} and filings from {sources[1]}; run `mise run deploy:data`")
for report in sorted(pathlib.Path(tickers).glob("*.json")):
    content = json.load(open(report))
    if content["facts_source"] != "edgar":
        sys.exit(f"{report} has filings from {content['facts_source']}; run `mise run deploy:data`")
    for field in ("valuation", "risk"):
        if content.get(field) is not None:
            sys.exit(f"{report} has a {field}, which is worked out from prices and may not be published; run `mise run deploy:data`")
evidence = pathlib.Path(path).with_name("evidence.json")
if evidence.exists():
    run = json.load(open(evidence))["run"]
    sources = (run["price_source"], run["facts_source"])
    if sources != ("tiingo", "edgar"):
        sys.exit(f"{evidence} has prices from {sources[0]} and filings from {sources[1]}; run `mise run deploy:data`")
    if run["universe"] != scores["universe"]:
        sys.exit(f"{evidence} is for {run['universe']}, not {scores['universe']}; run `mise run deploy:data`")
PY
rm -f data/deploy.sqlite
uv run eq import --scores "$scores" --tickers "$tickers" --db data/deploy.sqlite
# The function's disk is read-only, and SQLite cannot open a WAL database there.
uv run python -c "import sqlite3; sqlite3.connect('data/deploy.sqlite').execute('PRAGMA journal_mode=DELETE')"
