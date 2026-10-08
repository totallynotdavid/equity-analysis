# CLI

The `eq` command is installed by `index-cli`
([`packages/cli/index_cli/main.py`](../packages/cli/index_cli/main.py)). Run it
through uv from the repository root, for example `uv run eq run ...`.

| Command       | What it does                                                 | Details                         |
| ------------- | ------------------------------------------------------------ | ------------------------------- |
| `eq run`      | Fetches prices and filings, fits, scores, stores and exports | [below](#eq-run)                |
| `eq export`   | Writes the latest stored scores as JSON                      | [below](#eq-export)             |
| `eq coverage` | Lists names with few fundamentals in the stored filings      | [Fundamentals](FUNDAMENTALS.md) |
| `eq backtest` | Walks forward over the stored data and prints metrics        | [Backtest](BACKTEST.md)         |

A failure prints `eq: <message>` to stderr and exits non-zero.

## Ticker list files

A universe is a text file with one ticker per line. `#` starts a comment. Case
is ignored. The universe name is the file name without its extension, so
`universes/demo30.txt` is `demo30`.

`SPY` is the benchmark and cannot be listed. A repeated ticker, or a file with
no tickers, is an error. The list is fixed: it does not know which companies
were members on a past date.
[`read_universe`](../packages/core/index_core/universe.py)

## eq run

```bash
uv run eq run --universe universes/demo30.txt --prices synthetic --filings synthetic
```

| Option       | Meaning                                             | Default                    |
| ------------ | --------------------------------------------------- | -------------------------- |
| `--universe` | Ticker list file. Required.                         |                            |
| `--prices`   | `tiingo` for real prices, `synthetic` for fake ones | `tiingo`                   |
| `--filings`  | `edgar` for real filings, `synthetic` for fake ones | `edgar`                    |
| `--start`    | First price date, `YYYY-MM-DD`                      | `--end` minus 2190 days    |
| `--end`      | Last price date, and the last filing date used      | today                      |
| `--db`       | SQLite file                                         | [see below](#the-database) |
| `--out`      | JSON file to write                                  | `outputs/scores.json`      |

`eq run` does this, in order:

1. Fetches daily prices for SPY and every ticker over the whole range. It
   fetches the range again on each run, because adjusted prices change after a
   dividend or split.
2. Fetches every company's filed facts up to `--end`. A ticker the SEC does not
   list has no filings. `eq run` names those tickers on stderr, and their
   fundamentals stay missing.
3. Builds the features, fits the model and scores the latest date
   ([Model](MODEL.md)).
4. Stores the run in the database and writes `--out` ([Outputs](OUTPUTS.md)).

With a synthetic source, the prices or filings are generated, and `eq run` says
on stderr that the scores mean nothing.

Two errors mean the range is too short.
`N labelled dates are too few to fit a model` means fewer than 20 usable
snapshot dates ([Model](MODEL.md#fit)). Use an earlier `--start`.
`too little history at <date> for [...]` names tickers that lack a technical
feature on the latest date, such as a recent listing. Remove them from the list.

## Real data

`--prices tiingo` and `--filings edgar` each read one environment variable and
fail without it.

| Variable         | Meaning                                                     | Default |
| ---------------- | ----------------------------------------------------------- | ------- |
| `TIINGO_API_KEY` | [Tiingo](https://www.tiingo.com/) API token for prices      | none    |
| `SEC_USER_AGENT` | Your name and an email address, sent to the SEC for filings | none    |

`INDEX_DB` selects the database ([below](#the-database)). `SCORES_JSON` is read
by the web page ([Outputs](OUTPUTS.md#web-page)).

The SEC asks every client to identify itself, so `SEC_USER_AGENT` must hold a
contact such as `Jane Doe jane@example.com`. The client waits 0.12 seconds
between requests to stay under the SEC's limit of 10 a second.

A database that holds synthetic data rejects real data ([below](#the-database)),
so after a synthetic run give the real run a new file. `INDEX_DB` makes every
later command read that file too:

```bash
export INDEX_DB=data/real.sqlite
TIINGO_API_KEY=... SEC_USER_AGENT="Jane Doe jane@example.com" \
  uv run eq run --universe universes/demo30.txt
```

Tiingo costs one request per ticker plus one for SPY: 31 for `demo30`. The SEC
costs one request for its ticker list and at most one per ticker.

## The database

The database is one SQLite file. It holds prices, filings and every saved run.

- Every command uses the file given by `--db`, else `$INDEX_DB`, else
  `data/index.sqlite`. `eq export`, `eq coverage` and `eq backtest` open it
  read-only. When it does not exist, they fail and name the other `.sqlite`
  files beside it.
- A run is keyed by universe and as-of date. Running the same universe again on
  the same date replaces that run. Other universes and dates stay.
- A database holds prices from one source and filings from one source. To switch
  between `synthetic` and `tiingo`, pass a new `--db` file. A mismatch stops
  with `this database holds synthetic prices; use a new database for tiingo`.

## eq export

```bash
uv run eq export --universe demo30 --out outputs/scores.json
```

Writes the stored run with the latest as-of date. `--universe NAME` limits the
choice to one universe. Without it, the latest run of any universe is written.
`--db` and `--out` work as for `eq run`.
