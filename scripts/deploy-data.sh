#!/usr/bin/env bash
# Scores the demo universe on synthetic data into data/, for the deployed page and API.
set -euo pipefail

uv sync --all-packages --locked
uv run eq run --universe universes/demo30.txt --prices synthetic --filings synthetic \
  --db data/deploy.sqlite --out data/deploy-scores.json
# The function's disk is read-only, and SQLite cannot open a WAL database there.
uv run python -c "import sqlite3; sqlite3.connect('data/deploy.sqlite').execute('PRAGMA journal_mode=DELETE')"
