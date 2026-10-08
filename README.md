# index

An internal tool that ranks US stocks with a daily score from 1 to 10. The score
is the decile of a LightGBM model's estimated probability that a stock beats SPY
over the next 63 trading days.

**Experimental, not validated.** The model uses about 20 technical features of
daily prices and 14 fundamental ratios from SEC filings. It is fitted once on a
chronological, purged split. `eq backtest` measures it walk-forward, but the
data is a fixed list of today's names with no point-in-time membership, so the
numbers carry survivorship bias. Nothing here is investment advice or a
recommendation to buy or sell any security, and you can lose money.

## Quick start

You need [uv](https://docs.astral.sh/uv/) and Python 3.14. uv installs the
Python it needs. Install the workspace once:

```bash
git clone https://github.com/totallynotdavid/equity-analysis
cd equity-analysis
uv sync --all-packages
```

`eq run` fetches prices and filings, builds features, fits the model, stores the
scores in SQLite and writes `outputs/scores.json`. Without keys, run it on
synthetic prices and filings. They are fake, so the scores mean nothing:

```bash
uv run eq run --universe universes/demo30.txt --source synthetic
```

With real data, set `TIINGO_API_KEY` (a free Tiingo account is enough for the 31
requests of the demo universe) and `SEC_USER_AGENT`, then leave out `--source`.
The SEC asks every client to identify itself, so the user agent must hold your
name and an email address:

```bash
TIINGO_API_KEY=... SEC_USER_AGENT="Jane Doe jane@example.com" \
  uv run eq run --universe universes/demo30.txt
```

`outputs/scores.json` holds an `as_of` date, the `universe` name (the universe
file name without extension) and one row per ticker with its `rank` (1 is best),
integer `score` and `prob`. A database keeps one run per universe and date, so
runs over different universes do not replace each other; `eq export` writes the
latest run, or one universe with `--universe NAME`. Tiingo's free data is
licensed for internal use only. A database holds prices from one source and
filings from one source, so use a new `--db` file to switch between synthetic
and real data.

| Setting          | Meaning                                        | Default                  |
| ---------------- | ---------------------------------------------- | ------------------------ |
| `TIINGO_API_KEY` | Tiingo API token, read when `--source tiingo`  | none                     |
| `SEC_USER_AGENT` | Name and email sent to EDGAR, read with Tiingo | none                     |
| `INDEX_DB`       | SQLite file shared by `eq` and the API         | `data/index.sqlite`      |
| `SCORES_JSON`    | File the web build reads                       | `../outputs/scores.json` |

## Fundamentals

The fundamental features come from the SEC's XBRL `companyfacts` data. Every
fact carries the date it was filed, and a fact is used only from the first
trading day after that date. A later filing that restates a period replaces the
earlier value from its own filing date on, never before it. Nothing is filled
backward: before a company's first filing its fundamentals are missing, and a
name with no filings keeps its technical features. A value is also missing once
its period ended more than 460 days before the date, so a company that stops
filing is not scored on old numbers.

Trailing twelve-month figures add the latest fiscal year to the year-to-date
quarters and subtract the year-to-date of a year before. The ratios are
valuation (sales, EBITDA, earnings and free cash flow against enterprise value
or market value), profitability and margins, growth, leverage, accruals, share
dilution and asset growth. Market value is the filed share count times the price
of that day, corrected for splits since the count was filed. Capital spending is
read as a positive amount paid, because filers differ on its sign. Like the
technical features, each ratio is ranked within its date.

`eq coverage --universe universes/demo30.txt` lists the names with fewer than
`--min-concepts` of the 13 concepts filed recently, so a name that a ticker
change or an unusual taxonomy leaves bare is visible. A missing value stays
missing, and the model reads it as missing.

## Backtest

`eq backtest` reads the prices and filings that `eq run` stored, so run that
first with a `--start` early enough for several years of training. It refits the
model every quarter on all labelled weekly snapshots whose 63-day label window
closed before the quarter began, and predicts only that quarter. The settings
are frozen, so a backtest never tunes them.

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

## Layout

A [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/) of
three Python packages and an Astro site:

```
├── packages/
│   ├── core/   index-core: sources, store, features, model, backtest
│   ├── cli/    index-cli: the `eq` command
│   └── api/    index-api: read-only FastAPI over the SQLite file
├── universes/  ticker lists, one per line
├── web/        Astro site that renders outputs/scores.json
├── pyproject.toml   workspace members, ruff, mypy and pytest settings
└── mise.toml        tool versions and tasks
```

`index-core` holds all the logic as functions over data, with no interface. Only
the price and filings sources touch the network. Prices come through a
`PriceSource` (Tiingo, or a synthetic generator for tests and offline runs) and
filings through a `FilingsSource` (EDGAR, or a synthetic generator). The CLI and
API only call into it.

`index-api` serves `GET /scores`, the latest scores in the JSON that `eq export`
writes, and `GET /health`, the status and the as-of date of the latest scores
(`null` until scores exist). The `web` site reads `outputs/scores.json` at build
time, so run `eq run` first and rebuild after each run.

## Development

[mise](https://mise.jdx.dev/getting-started.html) provisions uv and Bun and runs
the tasks in [`mise.toml`](mise.toml). Without mise, install uv and Bun yourself
and run the commands in each task.

```bash
mise install
mise run install   # uv sync --all-packages --locked, bun install in web/
```

| Task            | What it does                                        |
| --------------- | --------------------------------------------------- |
| `mise run cli`  | the `eq` command, for example `mise run cli -- run` |
| `mise run api`  | the API with reload at `http://127.0.0.1:8000/docs` |
| `mise run web`  | the Astro dev server at `http://localhost:4321`     |
| `mise run fix`  | format and lint the Python code with ruff           |
| `mise run mypy` | type-check with mypy                                |

The checks that CI runs, from the repository root:

```bash
uv run ruff check . --no-fix
uv run ruff format --check .
uv run mypy .
uv run pytest                  # about 2.5 minutes, no network
cd web && bun run build
```

The tests run on synthetic prices and filings and on a small hand-written EDGAR
payload (`packages/core/tests/fixtures`). They train real models, so they take
minutes, and they make no network calls.
