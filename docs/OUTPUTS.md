# Outputs

A run reaches readers in three ways: JSON files, a read-only API and a web page.
They show the same shapes,
[`ScoresReport`](../packages/core/index_core/report.py) for the scores,
`TickerReport` for the page of one ticker and `EvidenceReport` for the proof
page.

## scores.json

`eq run` and `eq export` write `outputs/scores.json` unless `--out` says
otherwise ([CLI](CLI.md)). The deployed page and API serve a file of this shape
([Deploy](DEPLOY.md)). `eq import` loads such a file into a database.

| Field                | Meaning                                                    |
| -------------------- | ---------------------------------------------------------- |
| `status`             | Always `experimental, not validated`                       |
| `as_of`              | The trading day the scores are for                         |
| `universe`           | The universe file's name without its extension             |
| `price_source`       | Where the prices came from: `tiingo` or `synthetic`        |
| `facts_source`       | Where the filings came from: `edgar` or `synthetic`        |
| `horizon_days`       | The label horizon, 63 trading days                         |
| `model.train_rows`   | Rows the model was fitted on                               |
| `model.holdout_rows` | Rows in the later holdout the model was measured on        |
| `model.holdout_auc`  | AUC on that holdout, `null` when it holds only one outcome |
| `rows[].ticker`      | The ticker                                                 |
| `rows[].rank`        | 1 is the highest probability. Ranks are unique             |
| `rows[].score`       | Decile from 1 to 10. 10 is the top tenth                   |
| `rows[].prob`        | The model's probability of beating SPY over `horizon_days` |
| `left_out[].ticker`  | A member of the run that is not in `rows`                  |
| `left_out[].reason`  | `no prices`, `no filings` or `other company`               |

Rows are ordered by rank. How the score derives from `prob` is in
[Model](MODEL.md#the-score).

The two sources are separate fields, and a run stores the ones it was made from
([CLI](CLI.md#the-database)). A run with `--prices tiingo --filings synthetic`
has `price_source` `tiingo` and `facts_source` `synthetic`. A database made by
`eq import` reports the file's sources. A file may omit `left_out`, which means
none.

## Ticker reports

`eq run` and `eq export` write one file per ticker, `tickers/<TICKER>.json`
beside `--out` unless `--tickers` says otherwise ([CLI](CLI.md)), on one line.
`eq import --tickers` loads them. A ticker with no filings has none.

| Field                    | Meaning                                                                                                                                                                                            |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ticker`, `universe`     | The ticker and the universe file's name                                                                                                                                                            |
| `as_of`                  | The trading day. Only filings made before it are used                                                                                                                                              |
| `facts_source`           | Where the filings came from: `edgar` or `synthetic`                                                                                                                                                |
| `name`, `cik`            | The company and its SEC identifier                                                                                                                                                                 |
| `sic`, `sic_description` | The SEC's industry code and its title                                                                                                                                                              |
| `sector`                 | The GICS sector from the universe's companies file, `null` without one                                                                                                                             |
| `series[]`               | One per metric the company has ([Fundamentals](FUNDAMENTALS.md#ticker-reports)): `metric`, `group`, `label`, `unit` (`USD`, `USD/share`, `shares` or `ratio`)                                      |
| `series[].pct_universe`  | Percent of the universe at or below the latest value, `null` if too few                                                                                                                            |
| `series[].pct_sector`    | The same within the sector                                                                                                                                                                         |
| `series[].points[]`      | The latest twelve periods, oldest first: `end`, `value`, `formula`, `parts`                                                                                                                        |
| `valuation`              | What the price implies ([Valuation](VALUATION.md)): `status` is `ok` with a `model`, or `not_meaningful` with a `reason`. A file made before it has none. The web pages do not read it             |
| `risk`                   | How the stock behaves ([Risk](RISK.md)): `status` is `ok` with a `fit`, or `insufficient_history` or `not_estimable` with a `reason`. A file made before it has none. The web pages do not read it |
| `parts[]`                | A filed figure: `metric`, `sign` (1 or -1), `tag`, `start` (`null` for a balance), `end`, `value` and its `filing`                                                                                 |
| `filing`                 | `accn`, `form`, `fy`, `fp`, `filed` and `url`, the EDGAR page that lists the filing's documents                                                                                                    |

The `value` of a point is the sum of `sign * value` over its `parts`, or, where
`formula` is set, that formula over the parts. Every figure on the page is a
part, so it can be checked against the filing it links to.

## evidence.json

`eq run` and `eq export` write the stored backtest of the scored universe to
`evidence.json` beside `--out`, or to `--evidence` for `eq export`.
`eq backtest` stores the backtest and `eq export` reads it back, so the file
holds exactly what the backtest measured
([Backtest](BACKTEST.md#the-stored-backtest)). When the universe has no stored
backtest, the command deletes the file and says so, so an earlier universe's
evidence is never published beside new scores.

| Field      | Meaning                                                                                                                                                                                                               |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `run`      | What was measured: `universe`, `period` (`development` or `holdout`), the dates, the counts of snapshot dates, months, stock-dates and independent windows, the base rate, the horizon, the cost and the data sources |
| `verdict`  | `candidate`, `lightgbm_stays`, `decided` (a held-out run), the three `checks` with their `value` and `passed`, and `shown`                                                                                            |
| `models`   | For each model: mean rank IC, t-statistic, AUC, top-decile hit rate and spread, each with a 95% interval; the top and spread portfolios; the cost ladder; the Deflated Sharpe; the five-factor alpha                  |
| `by_year`  | The mean rank IC of each model in each calendar year                                                                                                                                                                  |
| `by_score` | The hit rate and mean excess return for each LightGBM score                                                                                                                                                           |
| `features` | The rank IC of each feature alone, with its family, and `feature_hurdle_t`, the t-statistic a new factor needs                                                                                                        |
| `trials`   | The ledger of every specification measured on the database, with its Sharpe ratio                                                                                                                                     |

`verdict.shown` is computed: it is true only when the run is `holdout`, the
candidate is `lightgbm` and every check passed. The score is the one LightGBM
makes, so a baseline that passes the checks does not show it. A development run
has no verdict, because the months it measures chose the candidate. Every other
number in the file is rounded only where the web page prints it.

## API

`index-api` is a read-only FastAPI app
([`packages/api/index_api/main.py`](../packages/api/index_api/main.py)). It
reads the database at `$INDEX_DB`, else `data/index.sqlite`, on every request
([CLI](CLI.md#the-database)).

```bash
mise run api   # http://127.0.0.1:8000/docs
```

Without mise: `uv run uvicorn index_api.main:app --reload`.

| Endpoint                | Returns                                                                   |
| ----------------------- | ------------------------------------------------------------------------- |
| `GET /scores`           | The latest run of any universe, as in `scores.json`. 404 before a run     |
| `GET /tickers/{ticker}` | The latest [ticker report](#ticker-reports). 404 for a ticker without one |
| `GET /health`           | `{"status": "ok", "as_of": "2026-10-08"}`. `as_of` is `null` before a run |

CORS allows `GET` from `http://localhost:4321` and `http://127.0.0.1:4321`, the
web dev server.

## Web page

`web/` is an [Astro](https://astro.build/) site with four kinds of page:

| Page          | Shows                                                   |
| ------------- | ------------------------------------------------------- |
| `/`           | [The screener](#the-screener)                           |
| `/t/<TICKER>` | One page per ticker report                              |
| `/evidence`   | The proof page, rendered from `evidence.json`           |
| `/method`     | Every metric with its definition, and each data licence |

The build renders the files into the pages, so run `eq run` first and build
again after each run. The pages do not call the API.

From the repository root:

```bash
(cd web && bun install)   # once
mise run web              # dev server at http://localhost:4321
(cd web && bun run build) # writes web/dist
```

`mise run web` starts `astro dev` ([`web/package.json`](../web/package.json)),
which listens only on localhost.

`mise run site` runs `eq export` on the default database, then the build.

The build reads `../outputs/scores.json`, `../outputs/tickers` and
`../outputs/evidence.json`, relative to `web/`. Set `SCORES_JSON`, `TICKERS_DIR`
and `EVIDENCE_JSON` to read others. Any of them may be missing.

### The screener

The index page has one row for each ticker report, so every row links to a
ticker page. It shows the disclaimer, the as-of date and a table with the
ticker, name and sector, and a column for each metric of the reports, the
percentile of each in the universe and the age in days of the newest filing.
Nothing in it is worked out from prices. Figures are in the units the ticker
pages use: millions of dollars or shares, percent, and dollars a share. A figure
the report lacks, or whose newest period is more than 400 days older than the
report's newest, is empty.

The page warns unless `price_source` is `tiingo` and `facts_source` is `edgar`
or `null`, so a file without the two fields also warns. It lists the `left_out`
tickers with their reasons, the first 10 and a count of the rest. A build with
no ticker reports says there are none yet.

The table sorts by a click on a heading and filters by name, sector and a
minimum and maximum for each column. The columns shown are a choice too. A row
with no value for a filtered column is hidden. Nulls sort last either way, and
ties sort by ticker. The browser holds all rows, so nothing is requested while
the reader filters.

The address holds the state, so a copied link opens the same table. Typing
rewrites the address in place and a sort adds a history entry, so back returns
to the earlier table.

| Parameter          | Meaning                                                                                                    |
| ------------------ | ---------------------------------------------------------------------------------------------------------- |
| `q=text`           | Ticker or name contains the text, any case                                                                 |
| `sector=Name`      | Repeat for several: the sector is any of them                                                              |
| `min.KEY=n`        | The column `KEY` is at least `n`, in the unit shown                                                        |
| `max.KEY=n`        | The column `KEY` is at most `n`                                                                            |
| `col=KEY`          | Repeat to show only these columns, instead of the default columns                                          |
| `sort=KEY`, `-KEY` | Sort ascending, or descending with the minus. Default: the score, highest first, when shown, else `ticker` |

A key is the metric's name (`revenue`, `op_margin`), `KEY.pct` for its
percentile, or `filing_age`. An unknown key, a bound that is not a number and a
choice equal to the default are dropped.

The score is a column only when `evidence.json` is for the scored universe and
its verdict shows the score ([evidence.json](#evidencejson)). The table then
sorts by it, highest first. Otherwise a note says why it is withheld and links
`/evidence`: no backtest, a backtest of another universe, a development run, a
failed check, or a baseline that passed.

### Ticker pages

The build writes one static page for each ticker report, and stops with the
tickers named when a scored ticker has no report, so no scored ticker is left
without a page. A page shows the company, its sector and SIC, and a table for
each of the four groups of metrics with the latest value's percentile in the
universe and the sector, and up to 8 periods. A value that is a sum of filed
figures links to the filing of the figure that ends its period; a ratio shows
its formula instead. Below each metric a list shows every filed figure its
values are made of, with sign, tag, period and a link to its filing. A page of
`synthetic` filings warns that its figures mean nothing. Dollar and share
amounts that are whole millions or thousands are written in them, as the
statements are; others are written in full.

A page shows no price and no figure worked out from prices. The `valuation` and
`risk` of a report are not read, so a report that carries them shows none of it.
The Tiingo licence is for internal use, and the site publishes the scores and
their evaluation only.

### The evidence page

`/evidence` is rendered from `evidence.json` and holds no number of its own. It
has the run, the verdict with its three checks, the models, the portfolios, the
cost ladder, the Deflated Sharpe and the factor alpha, the rank IC by year, the
hit rate by score, each feature alone and the ledger of trials. A development
run says there is no verdict. Without the file the page says no backtest has
been stored and names the commands that make one.

### The method page

`/method` lists every metric the ticker reports hold with its group, unit and
definition: the formula of a derived metric, or the XBRL tags of a reported one.
It then lists the data sources, each with what it is used for, a link to its
terms and the terms in short.
