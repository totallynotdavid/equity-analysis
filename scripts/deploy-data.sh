#!/usr/bin/env bash
# Scores the demo universe on real prices and filings into deploy/scores.json,
# the file the Vercel build publishes. Needs TIINGO_API_KEY and SEC_USER_AGENT.
# Extra arguments go to `eq run`, for example `--end 2026-10-10`.
set -euo pipefail

uv sync --all-packages --locked
uv run eq run --universe universes/demo30.txt \
  --db data/real.sqlite --cache data/cache --out deploy/scores.json "$@"
