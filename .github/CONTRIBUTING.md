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

| Task             | What it does                                         |
| ---------------- | ---------------------------------------------------- |
| `mise run cli`   | The `eq` command, for example `mise run cli -- run`  |
| `mise run api`   | The API with reload at `http://127.0.0.1:8000/docs`  |
| `mise run web`   | The Astro dev server at `http://localhost:4321`      |
| `mise run site`  | Export the latest scores, then build the static page |
| `mise run fix`   | Format and lint the Python code with ruff            |
| `mise run mypy`  | Type-check with mypy                                 |
| `mise run check` | Every check below, as CI runs them                   |

## Checks

CI ([`ci.yml`](workflows/ci.yml)) runs `mise run install` and `mise run check`.
Run `mise run check` before you submit a change. It runs the `check:python` and
`check:web` tasks, which are these commands from the repository root:

```bash
uv run ruff check . --no-fix
uv run ruff format --check .
uv run mypy .
uv run pytest -q               # a few minutes, no network
(cd web && bunx prettier --check src astro.config.mjs)
(cd web && bun run build)
```

## Tests

The tests run on synthetic prices and filings and on EDGAR payloads in
[`packages/core/tests/fixtures`](../packages/core/tests/fixtures). They train
real models, so they take minutes, and they make no network calls.

`acme_companyfacts_handwritten.json` holds edge cases with known answers. To
record a real `companyfacts` response as a fixture, trimmed to the tags the
parser reads and to filings since `--since` (default `2022-01-01`), give the SEC
your contact:

```bash
SEC_USER_AGENT="Jane Doe jane@example.com" \
  uv run python packages/core/tests/record_companyfacts.py 320193 aapl
```

The file is written as `recorded_aapl_companyfacts.json` in the fixtures
directory.
