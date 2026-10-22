# CLI

The `eq` command is installed by `index-cli`
([`packages/cli/index_cli/main.py`](../packages/cli/index_cli/main.py)). Run it
through uv from the repository root, for example `uv run eq run ...`.

| Command       | What it does                                                 | Details                         |
| ------------- | ------------------------------------------------------------ | ------------------------------- |
| `eq run`      | Fetches prices and filings, fits, scores, stores and exports | [below](#eq-run)                |
| `eq filings`  | Fetches only the filings and company names of a universe     | [below](#eq-filings)            |
| `eq metrics`  | Recomputes the ticker reports from the stored filings        | [below](#eq-metrics)            |
| `eq export`   | Writes the latest stored scores and ticker reports as JSON   | [below](#eq-export)             |
| `eq import`   | Stores a scores file and ticker reports, for the API         | [below](#eq-import)             |
| `eq coverage` | Lists names with few fundamentals in the stored filings      | [Fundamentals](FUNDAMENTALS.md) |
| `eq factors`  | Stores the Fama-French five-factor daily returns             | [below](#eq-factors)            |
| `eq backtest` | Walks forward over the stored data and prints the evidence   | [Backtest](BACKTEST.md)         |

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
- `eq coverage` reports the members on the latest stored price date, or on
  `--as-of`.

A file `<name>.companies.csv` beside the universe file gives the SEC identifier
(CIK) and the current GICS sector of its tickers, one `ticker,cik,sector` per
line, with `#` comments. The ticker pages group stocks by the sector. A ticker
without a line has no CIK, so no filings, and the run leaves it out as
`no filings`. A line with an empty sector, as a name that left the index has,
gives the CIK and no sector percentile. A run does not consult the SEC's ticker
list: it holds today's tickers, and one may name another company than the member
it was. Only the [rebuild](#rebuilding-sp500txt) reads it, for the members of
today. A company that moved to a new registrant lists the new CIK first and the
earlier ones after it, joined with `+`, as `XOM` does: its filings from every
registrant make one history, and a figure links to the filing under the
registrant that filed it.

| File                                                        | Contents                                                                                     |
| ----------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| [`demo30.txt`](../universes/demo30.txt)                     | Today's 30 large caps, all members from 2000. For offline runs. It has survivorship bias.    |
| [`demo30.companies.csv`](../universes/demo30.companies.csv) | The CIK and sector of the 30                                                                 |
| [`sp500.txt`](../universes/sp500.txt)                       | S&P 500 members from 2011-01-01, including the names that left. 810 tickers, from Wikipedia. |
| [`sp500.companies.csv`](../universes/sp500.companies.csv)   | The CIK of every ticker and the sector of today's members                                    |
| [`sp500.former.csv`](../universes/sp500.former.csv)         | The reviewed CIK of each name that left, which the script reads                              |

### Rebuilding sp500.txt

```bash
uv run python universes/build_sp500.py
```

[`build_sp500.py`](../universes/build_sp500.py) fetches two Wikipedia pages with
a declared `User-Agent` (`--user-agent` changes it), reads their HTML tables and
walks the changes table backwards from today's members. It writes the page URLs,
the fetch date and the CC BY-SA 4.0 licence into the file header, and prints the
rows it could not place on stderr. `--since` moves the first date, and `--out`
the file. Beside it, it writes `sp500.companies.csv`: the GICS sector of today's
members, their CIK from the SEC's
[`company_tickers.json`](https://www.sec.gov/files/company_tickers.json), the
earlier registrants it lists in `PREDECESSORS`, and the CIK of each name that
left. The SEC file is fetched with `SEC_USER_AGENT` (set in `mise.toml`), which
the script requires. A current member that file does not list has
no CIK and no line, and the script names it on stderr.

A name that left is not in the SEC's ticker list, and its ticker may name
another company now, so its CIK comes from
[`sp500.former.csv`](../universes/sp500.former.csv). Each line is the `Security`
name that the changes table gives for the removal, a CIK, and a ticker when two
companies share a name. The table was filled by hand, checking each name against
EDGAR's company search and submissions. `--former` selects another file. The
script lists the names that left and have no line, and the lines that match no
name; a name without a line has no filings.

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
| `--cache`    | Directory for the responses of the SEC              | `data/cache`               |
| `--db`       | SQLite file                                         | [see below](#the-database) |
| `--out`      | JSON file to write                                  | `outputs/scores.json`      |
| `--tickers`  | Directory for the ticker reports, one file each     | `tickers` beside `--out`   |

`eq run` does this, in order:

1. Fetches daily prices for SPY and every ticker that was a member in the range,
   asking only for the bars the database lacks ([the cache](#the-cache)). A
   ticker Tiingo does not list, or lists with no bars in the range, has no
   prices, and one that Tiingo lists only as another company is dropped.
   `eq run` names those tickers on stderr and leaves them out. A missing SPY
   stops the run.
2. Fetches every company's filed facts up to `--end`, and its name and SIC code
   from the SEC's submissions API. A ticker with no CIK in the universe's
   companies file, or whose CIK the SEC has no filings for, has no filings.
   `eq run` names those tickers on stderr and leaves them out.
3. Builds the features, fits the model and scores the latest date
   ([Model](MODEL.md)).
4. Stores the run in the database and writes `--out` ([Outputs](OUTPUTS.md)).
   The tickers left out in steps 1 and 2 are in the file's `left_out`, with the
   reason. When none is left, the run stops with
   `no ticker has both prices and filings`.
5. Computes the [metrics and ticker reports](FUNDAMENTALS.md#ticker-reports) of
   every member that has filings, stores them and writes one file each to
   `--tickers`, replacing the `.json` files there.
6. Writes the stored [backtest](BACKTEST.md#the-stored-backtest) of the universe
   to `evidence.json` beside `--out` ([Outputs](OUTPUTS.md#evidencejson)). If
   there is none, it deletes that file and prints
   `no backtest stored for the universe; no <path>`.

With a synthetic source, the prices or filings are generated, and `eq run` says
on stderr that the scores mean nothing.

Two errors mean the range is too short.
`N labelled dates are too few to fit a model` means fewer than 20 usable
snapshot dates ([Model](MODEL.md#fit)). Use an earlier `--start`.
`too little history at <date> for [...]` names tickers that lack a technical
feature on the latest date, such as a recent listing. Remove them from the list.

## eq filings

```bash
uv run eq filings --universe universes/demo30.txt --filings edgar
```

Fetches and stores the filed facts and the company names of the universe, as
step 2 of [`eq run`](#eq-run) does, and no prices. `--filings`, `--start`,
`--end`, `--cache` and `--db` work as for `eq run`. It replaces the facts stored
before. Then [`eq coverage --as-of DATE`](FUNDAMENTALS.md#eq-coverage) reports
what the filings hold.

## eq metrics

```bash
uv run eq metrics --universe universes/demo30.txt
```

Recomputes the metrics and ticker reports from the stored filings, prices and
factors, without fetching. The reports are as of the last stored SPY date. Each
holds the [valuation](VALUATION.md) and the [risk](RISK.md) of its ticker, which
need the stored factors ([`eq factors`](#eq-factors)). `--tickers` selects the
directory (default `outputs/tickers`) and `--db` the database.

## Real data

`--prices tiingo` and `--filings edgar` each read one environment variable and
fail without it.

| Variable         | Meaning                                                | Default                            |
| ---------------- | ------------------------------------------------------ | ---------------------------------- |
| `TIINGO_API_KEY` | [Tiingo](https://www.tiingo.com/) API token for prices | none                               |
| `SEC_USER_AGENT` | The `User-Agent` header sent to the SEC for filings    | set by [`mise.toml`](../mise.toml) |

`INDEX_DB` selects the database ([below](#the-database)). `SCORES_JSON` is read
by the web page ([Outputs](OUTPUTS.md#web-page)).

The SEC refuses a request without a declared `User-Agent`, and `www.sec.gov`
also refuses a browser string. [`mise.toml`](../mise.toml) sets
`SEC_USER_AGENT` to `equity-analysis research bot`, with no name or email
address, so every `mise run` task, and every shell where mise is active in this
directory, has it. Outside mise, export the same value.

The client waits 0.12 seconds between requests to stay under the SEC's limit of
10 a second.

A database that holds synthetic data rejects real data ([below](#the-database)),
so after a synthetic run give the real run a new file. `INDEX_DB` makes every
later command read that file too:

```bash
export INDEX_DB=data/real.sqlite
TIINGO_API_KEY=... \
  uv run eq run --universe universes/demo30.txt
```

Tiingo costs one request per ticker whose bars are not stored, plus one for SPY:
31 for `demo30`, which the 72-second pause stretches to about 36 minutes on a
first run. The SEC costs two requests per ticker, its `companyfacts` and its
submissions, and one more `companyfacts` for each earlier registrant. A ticker
with no CIK in the universe's companies file costs none and is left out as
`no filings`.

### The cache

`--cache` holds the SEC and Kenneth French responses for one day, so a repeat
run costs no request to them. Pass a new directory to fetch them again. `data/`
is ignored by git. What the cache keeps and how it is written are in the
[cache rule](ARCHITECTURE.md#boundaries).

Tiingo's prices are not in that cache. The database is: `eq run` asks Tiingo
only for what `--db` lacks, whatever the day.

- A ticker already asked for from `--start` through `--end` is not asked for
  again. Neither is a former member already asked for through 100 days past its
  last day as a member, which holds the 64 trading days a label reads. A run
  that finds nothing to ask for makes no request.
- A ticker stored through an earlier date is asked for from the fifth bar before
  its last stored bar. When those five adjusted closes differ from the stored
  ones, a split or dividend has restated them, and `eq run` fetches that ticker
  whole once.
- Tickers never asked for come before the ones to bring up to date, so a monthly
  cap on symbols is spent on the gaps first.
- A ticker Tiingo does not know is recorded as asked, so a repeat run does not
  ask again until `--end` moves past the date it was asked about.
- Tiingo's public ticker list, one download without a key, gives the dates each
  ticker traded. A ticker none of whose listings overlaps the days it was a
  member is another company's, because Tiingo reuses tickers: it is not asked
  for, and `eq run` names it on stderr and in `left_out` with the reason
  `other company`. A ticker with no listing is not judged. The list is
  downloaded only when some ticker needs a request.

The SEC client leaves 0.12 seconds between requests it sends. The Tiingo client
leaves 72 seconds (3600 / 50) between price requests. When Tiingo answers HTTP
429, `eq run` stops with Tiingo's message. Each ticker is stored as it arrives,
so run the same command again after the limit resets and it carries on from the
first ticker that was not stored.

#### Running all of `sp500.txt`

The first fill asks Tiingo for `SPY` and every ticker that was a member in the
range: `members_between(start, end)` plus one, less the tickers that another
company reused. The two ranges that matter:

| Range                                         | Requests |    At 72 s each |
| --------------------------------------------- | -------: | --------------: |
| `--start 2012-01-01 --end 2026-10-09`         |      794 | 57168 s, 15.9 h |
| default start (2190 days), `--end 2026-10-09` |      613 | 44136 s, 12.3 h |

The time is the number of requests times 72 s. The counts are upper bounds.

Tiingo's free Starter plan allows 50 requests an hour, 1000 a day and 500 unique
symbols a month. The 794 symbols of the first range are above the monthly cap.
Run the same command until Tiingo answers 429, and again after the limit resets
in the next month: the second run asks first for the tickers the first never
reached, and only then updates the others. One month of the $30 Power plan
(10000 requests an hour) needs one run and makes the pause unnecessary. The
plans are on [Tiingo's pricing page](https://www.tiingo.com/about/pricing).

A repeat run on the same range asks for nothing. A run with a later `--end` asks
once for each ticker that is still a member, about 500 for today's index, and
for no former member whose label window is stored. Pin `--start` when you expect
to run again; the default start moves with `--end`, and a start that moves
forward needs nothing refetched.

Tiingo repeats a delisted company's last close, with zero volume, up to the end
of the range. The Tiingo source drops those bars. A ticker with no trades in the
range counts as having no prices.

## eq factors

```bash
uv run eq factors --source synthetic
uv run eq factors
```

| Option     | Meaning                                                  | Default                    |
| ---------- | -------------------------------------------------------- | -------------------------- |
| `--source` | `french` for the real file, `synthetic` for fake returns | `french`                   |
| `--cache`  | Directory where the download is kept for a day           | `data/cache`               |
| `--db`     | SQLite file                                              | [see below](#the-database) |

Downloads `F-F_Research_Data_5_Factors_2x3_daily_CSV.zip` from Kenneth French's
data library, which needs no key, and stores every day of Mkt-RF, SMB, HML, RMW,
CMA and RF as a fraction in `factor_returns`, replacing what was stored. It
prints how many days it stored and their range. The file is in percent and ends
a month or two before today.
[`french.py`](../packages/core/index_core/sources/french.py)

The factors do not depend on the universe. Run the command once per database
before [`eq run`](#eq-run) or [`eq metrics`](#eq-metrics), whose
[valuations](VALUATION.md#the-inputs) take the risk-free rate from it and the
[risk](RISK.md#the-window) of a ticker is regressed on it, and before
[`eq backtest`](BACKTEST.md#alpha), which needs 12 months of overlap with the
backtest for the alpha table. A database holds factors from one source, as it
does for prices ([below](#the-database)).

## The database

The database is one SQLite file. It holds prices, filings, company identities,
factors, the backtest ledger, every saved run, the latest ticker reports, the
latest backtest of each universe and period, and the `risk` and `exposures`
tables of the latest metrics.

- Every command uses the file given by `--db`, else `$INDEX_DB`, else
  `data/index.sqlite`. `eq export` and `eq coverage` open it read-only. When it
  does not exist, they fail and name the other `.sqlite` files beside it.
  `eq backtest` opens it for writing, because it adds to the `trials` ledger
  ([Backtest](BACKTEST.md#multiple-testing)) and stores its result
  ([Backtest](BACKTEST.md#the-stored-backtest)).
- A run is keyed by universe and as-of date. Running the same universe again on
  the same date replaces that run. Other universes and dates stay.
- A database holds prices from one source and filings from one source. To switch
  between `synthetic` and `tiingo`, pass a new `--db` file. A mismatch stops
  with `this database holds synthetic prices; use a new database for tiingo`.
  Factors are the same, with `french` and `synthetic`.
- An additive database layout change upgrades in place. Other changes fail to
  open with `has an older layout; delete it and run again`; those databases
  must be rebuilt.
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
`--db` and `--out` work as for `eq run`. It also writes the stored ticker
reports to `--tickers` (default `tickers` beside `--out`), replacing the `.json`
files there, and the stored backtest of the universe to `--evidence` (default
`evidence.json` beside `--out`), as step 6 of [`eq run`](#eq-run) does.

## eq import

```bash
uv run eq import --scores deploy/scores.json --tickers deploy/tickers \
  --db data/deploy.sqlite
```

Reads a file in the shape of `eq run`'s `--out` ([Outputs](OUTPUTS.md)) and
stores it as the run of its universe and as-of date. With `--tickers` it also
stores every ticker report file of that directory, replacing a stored report of
the same universe and ticker. The database then holds the scores and their two
sources and the reports, and no prices or filings, so `index-api` serves it and
`eq export` writes the files back unchanged. A file that is not a scores report
fails with the fields that are wrong. A ticker report of another universe or
another as-of date than the scores file fails the import and stores nothing.
`--db` works as for `eq run`.
