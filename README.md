# index

An internal tool that ranks US stocks with a daily score from 1 to 10. The score
is the decile of a LightGBM model's estimated probability that a stock beats SPY
over the next 63 trading days.

**Experimental, not validated.** The model uses about 20 technical features of
daily prices and is fitted once on a chronological, purged split. `eq backtest`
measures it walk-forward, but the data is a fixed list of today's names with no
point-in-time membership, so the numbers carry survivorship bias. Nothing here
is investment advice or a recommendation to buy or sell any security, and you
can lose money.

## Quick start

`eq run` fetches prices, builds features, fits the model, stores the scores in
SQLite and writes `outputs/scores.json`. Without a Tiingo key, run it on
synthetic prices. They are fake, so the scores mean nothing:

```bash
uv run eq run --universe universes/demo30.txt --source synthetic
```

With real prices, set `TIINGO_API_KEY` (a free Tiingo account is enough for the
31 requests of the demo universe) and leave out `--source`:

```bash
TIINGO_API_KEY=... uv run eq run --universe universes/demo30.txt
```

`outputs/scores.json` holds an `as_of` date, the `universe` name (the universe
file name without extension) and one row per ticker with its `rank` (1 is best),
integer `score` and `prob`. A database keeps one run per universe and date, so
runs over different universes do not replace each other; `eq export` writes the
latest run, or one universe with `--universe NAME`. Tiingo's free data is
licensed for internal use only. A database holds prices from one source only, so
use a new `--db` file to switch between synthetic and Tiingo.

| Setting          | Meaning                                       | Default                  |
| ---------------- | --------------------------------------------- | ------------------------ |
| `TIINGO_API_KEY` | Tiingo API token, read when `--source tiingo` | none                     |
| `INDEX_DB`       | SQLite file shared by `eq` and the API        | `data/index.sqlite`      |
| `SCORES_JSON`    | File the web build reads                      | `../outputs/scores.json` |

## Backtest

`eq backtest` reads the prices that `eq run` stored, so run that first with a
`--start` early enough for several years of training. It refits the model every
quarter on all labelled weekly snapshots whose 63-day label window closed before
the quarter began, and predicts only that quarter. The settings are frozen, so a
backtest never tunes them.

```bash
uv run eq run --universe universes/demo30.txt --source synthetic --start 2008-01-01
uv run eq backtest --universe universes/demo30.txt
```

It prints the out-of-sample rank IC with a Newey-West t-statistic, the per-date
AUC, the hit rate of scores 8 to 10 against the base rate, and the hit rate and
mean excess return of each score from 1 to 10, with 95% intervals that resample
whole months. Snapshots a week apart share most of their outcome, so the output
also states how many independent 63-day windows the period holds. That is the
effective sample, and it is small.

The last 24 months of predictions (`--holdout-months`) stay out of the metrics.
`--final` measures only those months. Use it once, before a public claim.

## Architecture

The project follows a monorepo structure managed with
[uv workspaces](https://docs.astral.sh/uv/concepts/projects/workspaces/#getting-started),
organizing code into independent Python packages. This structure supports
cross-package development, clear separation of concerns, and isolated dependency
management.

The `index-core` package implements all analytical logic as functions that
process data and return structured results, independent of any interface. Only
the price sources touch the network. The API and CLI packages provide interfaces
only.

The monorepo consists of four main components:

**Core package** ([`packages/core`](packages/core)) houses `index-core`, the
application's engine: price sources behind a `PriceSource` interface (Tiingo,
and a synthetic generator for tests and offline runs), the SQLite store,
technical features, the label, the model, the decile score and the walk-forward
backtest.

**CLI package** ([`packages/cli`](packages/cli)) provides `index-cli`, the `eq`
command, with `eq run`, `eq backtest` and `eq export`.

**API package** ([`packages/api`](packages/api)) contains `index-api`, a
read-only FastAPI server over the SQLite file. `GET /scores` returns the latest
scores, the same JSON that `eq export` writes. `GET /health` returns the status
and the as-of date of the latest scores (`null` until scores exist).

**Frontend application** ([`web`](web)) houses an Astro site. It renders
`outputs/scores.json` as a ranked table at build time, so run `eq run` first and
rebuild after each run.

## Technology stack

**Backend and CLI:** Built with Python 3.14+, using `uv` for package management.
The web API is powered by FastAPI.

**Frontend:** Developed with Astro, styled using Tailwind CSS + Starwind CSS,
and running on the Bun JavaScript runtime.

For the development stack, I use `mise` to manage task automation and to enforce
version consistency across environments. Python packages are linted and
type-checked with Ruff and Mypy (configured in the root
[`pyproject.toml`](pyproject.toml?plain=1#L18)), while the frontend is currently
formatted with Prettier.

## Development environment

First, install `mise` by following the official guide at
[mise.jdx.dev](https://mise.jdx.dev/getting-started.html) for your operating
system. You'll also need Git for version control. Once installed, `mise` will
automatically provision Python, `uv`, and Bun as defined in the configuration.

Clone the repository and enter the project directory:

```bash
git clone https://github.com/totallynotdavid/equity-analysis
cd equity-analysis
```

Set up the environment with:

```bash
mise install
mise run install
```

The first command installs all required tools from `.mise.toml`. The second
installs Python workspace dependencies from `uv.lock` and frontend dependencies
with `bun install` inside `web/`. See [mise.toml](mise.toml?plain=1#L10) for
more details.

By default, `uv` also creates and manages a virtual environment, enabled through
the `uv_venv_auto = true` setting.

## Running the system

Running the application typically requires two or three terminal sessions,
depending on which components you need.

**API server.** From the repository root, run:

```
mise run api
```

This starts Uvicorn with hot reloading. The API will be available at
`http://127.0.0.1:8000`, with interactive docs at `http://127.0.0.1:8000/docs`.

**CLI tool.** The CLI can be used independently of the other components:

```
mise run cli -- --help
```

**Tests.** The tests use synthetic prices and make no network calls:

```
uv run pytest
```

**Frontend.** To launch the development server, run:

```
mise run web
```

The interface will be available at `http://localhost:4321` on your machine, and
can also be accessed from other devices on your local network using your
computer’s IP address.

## Project structure

The monorepo structure:

```
├── .mise.toml                      # Tool versions and task definitions
├── pyproject.toml                  # Workspace configuration, ruff and mypy settings
├── packages/
│   ├── core/
│   │   ├── index_core/             # Analytical engine
│   │   └── pyproject.toml          # Core package definition
│   ├── cli/
│   │   ├── index_cli/              # Command-line interface
│   │   └── pyproject.toml          # CLI package definition
│   └── api/
│       ├── index_api/              # Web API
│       └── pyproject.toml          # API package definition
├── universes/                      # Ticker lists, one per line
├── web/                            # Astro frontend application
│   ├── src/
│   ├── astro.config.mjs
│   └── package.json
```

The root `pyproject.toml` acts as the central configuration. Its
`[tool.uv.workspace]` section defines the monorepo members and centralizes tool
settings for consistency. Each package has its own `pyproject.toml`, declaring
dependencies and referencing local packages (e.g. `index-core`)
[1](packages/api/pyproject.toml?plain=1#L13)
[2](packages/cli/pyproject.toml?plain=1#L14).
