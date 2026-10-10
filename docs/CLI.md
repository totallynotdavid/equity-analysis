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

## Universe files

A universe is a text file with one membership per line: `ticker,start[,end]`. A
name is a member from `start` through the day before `end`. A line without
`end`, or with it blank, is still a member. Dates are `YYYY-MM-DD`. `#` starts a
comment. The universe name is the file name without its extension, so
`universes/demo30.txt` is `demo30`.

```text
AAPL,2000-01-03
GE,2011-01-01,2018-06-26
TSLA,2020-12-21
```

A name can have several lines, for a name that left and came back. Their dates
cannot overlap. `SPY` is the benchmark and cannot be listed. A file with no
tickers, a line that is not `ticker,start[,end]`, a date that is not ISO, or an
`end` that is not after `start` is an error that names the file and line.
[`read_universe`](../packages/core/index_core/universe.py)

Every command uses the dates:

- `eq run` fetches prices and filings for the names that were members at some
  point between `--start` and `--end`, and scores the members on the latest
  date.
- The labels, the model's training rows and the backtest use a stock on a date
  only when it was a member then. The cross-sectional ranks of the features are
  taken among that date's members. A name stays in the history until its `end`.
- `eq coverage` reports the members on the latest stored price date.

| File                                    | Contents                                                                                     |
| --------------------------------------- | -------------------------------------------------------------------------------------------- |
| [`demo30.txt`](../universes/demo30.txt) | Today's 30 large caps, all members from 2000. For offline runs. It has survivorship bias.    |
| [`sp500.txt`](../universes/sp500.txt)   | S&P 500 members from 2011-01-01, including the names that left. 810 tickers, from Wikipedia. |

### Rebuilding sp500.txt

```bash
uv run python universes/build_sp500.py
```

[`build_sp500.py`](../universes/build_sp500.py) fetches two Wikipedia pages with
a declared `User-Agent` (`--user-agent` changes it), reads their HTML tables and
walks the changes table backwards from today's members. It writes the page URLs,
the fetch date and the CC BY-SA 4.0 licence into the file header, and prints the
rows it could not place on stderr. `--since` moves the first date, and `--out`
the file.

The changes table is dense from 2011: at least 16 rows a year. Before that it
has at most 13 a year, so the script's default start is 2011-01-01 and an
earlier `--since` gives a list that is missing members. The table lists tickers,
not companies, so a company that changed its ticker has a gap or a warning where
the table names only one of the two. A current member that the walk never sees
added starts on `--since`, or on its "Date added" when that is later. The table
can miss a change, and Wikipedia's editors can fix it later, so a rebuild can
differ from the checked-in file.

## eq run

```bash
uv run eq run --universe universes/demo30.txt --prices synthetic --filings synthetic
```

| Option       | Meaning                                             | Default                    |
| ------------ | --------------------------------------------------- | -------------------------- |
| `--universe` | Universe file. Required.                            |                            |
| `--prices`   | `tiingo` for real prices, `synthetic` for fake ones | `tiingo`                   |
| `--filings`  | `edgar` for real filings, `synthetic` for fake ones | `edgar`                    |
| `--start`    | First price date, `YYYY-MM-DD`                      | `--end` minus 2190 days    |
| `--end`      | Last price date, and the last filing date used      | today                      |
| `--cache`    | Directory for the responses of Tiingo and the SEC   | `data/cache`               |
| `--db`       | SQLite file                                         | [see below](#the-database) |
| `--out`      | JSON file to write                                  | `outputs/scores.json`      |

`eq run` does this, in order:

1. Fetches daily prices for SPY and every ticker that was a member in the range,
   over the whole range. It asks for the range on every run, because adjusted
   prices change after a dividend or split; a repeat run inside the
   [cache](#the-cache) day reuses the cached responses. A ticker Tiingo does not
   list, or lists with no bars in the range, has no prices. `eq run` names those
   tickers on stderr and leaves them out. A missing SPY stops the run.
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

Tiingo costs one request per ticker plus one for SPY: 31 for `demo30`, which the
72-second pause stretches to about 36 minutes when none is cached. The SEC costs
one request for its ticker list and at most one per ticker.

### The cache

`--cache` holds the Tiingo and SEC responses, so a run that stops can carry on
and a repeat run costs no requests.

A repeat run reuses the cached responses for one day and stores the prices of
the first run. Tiingo's URL holds the dates, so `--start` and `--end` must match
between runs: pin `--end` when you expect to run again. Pass a new directory to
fetch everything again. `data/` is ignored by git. What the cache keeps and how
it is written are in the [cache rule](ARCHITECTURE.md#boundaries).

The SEC client leaves 0.12 seconds between requests it sends. The Tiingo client
leaves 72 seconds (3600 / 50). A response from the cache costs neither a wait
nor a request. When Tiingo answers HTTP 429, `eq run` stops with Tiingo's
message. Everything fetched so far is cached, so run the same command again
after the limit resets and it carries on from the first ticker that was not
fetched.

#### Running all of `sp500.txt`

One run asks Tiingo for `SPY` and every ticker that was a member in the range:
`members_between(start, end)` plus one. The two ranges that matter:

| Range                                         | Requests |    At 72 s each |
| --------------------------------------------- | -------: | --------------: |
| `--start 2012-01-01 --end 2026-10-09`         |      794 | 57168 s, 15.9 h |
| default start (2190 days), `--end 2026-10-09` |      613 | 44136 s, 12.3 h |

The time is the number of requests times 72 s. Only requests that miss the cache
count, so a run after an interruption pays for the tickers still to fetch. 50
requests an hour was measured on one free key. It is not a guarantee from
Tiingo.

An entry lasts one day, so a run that is resumed more than a day after its first
tickers were fetched asks for those tickers again.

Tiingo repeats a delisted company's last close, with zero volume, up to the end
of the range. The Tiingo source drops those bars. A ticker with no trades in the
range counts as having no prices.

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
- A database written by an older version fails to open with
  `has an older layout; delete it and run again`. Databases are rebuilt, not
  migrated.
- Prices include each day's cash dividend and split factor. The fundamental
  features read the split factor to correct a filed share count.
- A ticker with a dot, such as `BRK.B`, is looked up as `BRK-B` at Tiingo and
  the SEC. The database keeps the ticker as written in the universe file.

## eq export

```bash
uv run eq export --universe demo30 --out outputs/scores.json
```

Writes the stored run with the latest as-of date. `--universe NAME` limits the
choice to one universe. Without it, the latest run of any universe is written.
`--db` and `--out` work as for `eq run`.
