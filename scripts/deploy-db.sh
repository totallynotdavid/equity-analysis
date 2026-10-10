#!/usr/bin/env bash
# Loads the committed deploy/scores.json into data/deploy.sqlite for the API.
# Refuses a file whose prices or filings are not real.
set -euo pipefail

scores=deploy/scores.json

uv sync --all-packages --locked
uv run python - "$scores" <<'PY'
import json
import sys

path = sys.argv[1]
scores = json.load(open(path))
sources = (scores["price_source"], scores["facts_source"])
if sources != ("tiingo", "edgar"):
    sys.exit(f"{path} has prices from {sources[0]} and filings from {sources[1]}; run `mise run deploy:data`")
PY
rm -f data/deploy.sqlite
uv run eq import --scores "$scores" --db data/deploy.sqlite
# The function's disk is read-only, and SQLite cannot open a WAL database there.
uv run python -c "import sqlite3; sqlite3.connect('data/deploy.sqlite').execute('PRAGMA journal_mode=DELETE')"
