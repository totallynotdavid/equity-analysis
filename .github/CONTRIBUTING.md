# Contributing

Read [the architecture](../docs/ARCHITECTURE.md) for the code map first.

## Set up

Install the system and Python requirements in the
[README](../README.md#get-started). Then use
[mise](https://mise.jdx.dev/getting-started.html) to provision uv and Bun and to
run the tasks in [`mise.toml`](../mise.toml):

```bash
mise install
mise run install   # uv sync --all-packages --locked, bun install in web/
```

Without mise, install uv and Bun yourself and run the commands in each task.

| Task                   | What it does                                                                |
| ---------------------- | --------------------------------------------------------------------------- |
| `mise run cli`         | The `eq` command, for example `mise run cli -- run`                         |
| `mise run api`         | The API with reload at `http://127.0.0.1:8000/docs`                         |
| `mise run web`         | The Astro dev server at `http://localhost:4321`                             |
| `mise run site`        | Export the latest scores, then build the static page                        |
| `mise run deploy:data` | Refresh `deploy/scores.json` from real prices ([Deploy](../docs/DEPLOY.md)) |
| `mise run fix`         | Format and lint the Python code with ruff                                   |
| `mise run mypy`        | Type-check with mypy                                                        |
| `mise run check`       | Every check below except the end-to-end backtests                           |
| `mise run check:e2e`   | The tests marked `e2e`: backtests on a long history                         |

## Checks

CI ([`ci.yml`](workflows/ci.yml)) runs `mise run install`, then `mise run check`
in one job and `mise run check:e2e` in another. Run `mise run check` before you
submit a change, and `mise run check:e2e` when you touch the backtest, its
options, the ledger or the universe membership. `check` runs the `check:python`
and `check:web` tasks, which are these commands from the repository root:

```bash
uv run ruff check . --no-fix
uv run ruff format --check .
uv run mypy .
uv run pytest -q -m 'not e2e'  # minutes, no network
(cd web && bunx prettier --check src tests astro.config.mjs vitest.config.ts)
(cd web && bun run test)
(cd web && bun run build)
```

An `e2e` test runs one or several backtests on a long history, or, for the
membership tests, one `eq run` and one backtest. `check:e2e` is
`uv run pytest -q -m e2e`, and a plain `uv run pytest` runs both.

## Tests

The tests run on synthetic prices and filings and on EDGAR payloads in
[`packages/core/tests/fixtures`](../packages/core/tests/fixtures). They train
real models, so they take minutes, and they make no network calls.

The web tests in [`web/tests`](../web/tests) render the pages for each pair of
sources.

`acme_companyfacts_handwritten.json` holds edge cases with known answers. To
record a real `companyfacts` response as a fixture, trimmed to the tags the
parser reads and to filings since `--since` (default `2022-01-01`), give each
company as `TICKER:CIK`, with the `SEC_USER_AGENT` that `mise.toml` sets
([the CLI](../docs/CLI.md#real-data)):

```bash
uv run python packages/core/tests/record_companyfacts.py AAPL:320193
```

The file is written as `recorded_aapl_companyfacts.json` in the fixtures
directory.
