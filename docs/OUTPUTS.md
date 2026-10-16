# Outputs

A run reaches readers in three ways: a JSON file, a read-only API and a web
page. All three show the same shapes,
[`ScoresReport`](../packages/core/index_core/report.py) for the scores and
`TickerReport` for the page of one ticker.

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
| `left_out[].reason`  | `no prices` or `no filings`                                |

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

| Field                    | Meaning                                                                                                                                                       |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ticker`, `universe`     | The ticker and the universe file's name                                                                                                                       |
| `as_of`                  | The trading day. Only filings made before it are used                                                                                                         |
| `facts_source`           | Where the filings came from: `edgar` or `synthetic`                                                                                                           |
| `name`, `cik`            | The company and its SEC identifier                                                                                                                            |
| `sic`, `sic_description` | The SEC's industry code and its title                                                                                                                         |
| `sector`                 | The GICS sector from the universe's companies file, `null` without one                                                                                        |
| `series[]`               | One per metric the company has ([Fundamentals](FUNDAMENTALS.md#ticker-reports)): `metric`, `group`, `label`, `unit` (`USD`, `USD/share`, `shares` or `ratio`) |
| `series[].pct_universe`  | Percent of the universe at or below the latest value, `null` if too few                                                                                       |
| `series[].pct_sector`    | The same within the sector                                                                                                                                    |
| `series[].points[]`      | The latest twelve periods, oldest first: `end`, `value`, `formula`, `parts`                                                                                   |
| `parts[]`                | A filed figure: `metric`, `sign` (1 or -1), `tag`, `start` (`null` for a balance), `end`, `value` and its `filing`                                            |
| `filing`                 | `accn`, `form`, `fy`, `fp`, `filed` and `url`, the EDGAR page that lists the filing's documents                                                               |

The `value` of a point is the sum of `sign * value` over its `parts`, or, where
`formula` is set, that formula over the parts. Every figure on the page is a
part, so it can be checked against the filing it links to.

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

`web/` is an [Astro](https://astro.build/) site with an index page and one page
per ticker report, `/t/<TICKER>`. The build renders the scores file into the
index page, so run `eq run` first and build again after each run. In the
browser, the page then fetches `/scores` from the API and renders that over the
build's scores. `PUBLIC_API_URL` sets the API's base URL, `/api` by default.
When the fetch fails, as with `mise run web` and no API, the build's scores
stay.

From the repository root:

```bash
(cd web && bun install)   # once
mise run web              # dev server at http://localhost:4321
(cd web && bun run build) # writes web/dist
```

`mise run web` starts `astro dev` ([`web/package.json`](../web/package.json)),
which listens only on localhost.

`mise run site` runs `eq export` on the default database, then the build.

The build reads `../outputs/scores.json`, relative to `web/`. Set `SCORES_JSON`
to read another file. The page shows the disclaimer, the as-of date and a table
of rank, ticker and score. It warns unless `price_source` is `tiingo` and
`facts_source` is `edgar` or `null`, so a file without the two fields also
warns. It lists the `left_out` tickers with their reasons, the first 10 and a
count of the rest. Without a file the page says there are no scores yet. A
ticker links to its page only when the build made one, so a build without ticker
reports links nowhere, and the page rendered again from the API links the same
tickers.

The build reads the ticker reports from `../outputs/tickers`, relative to
`web/`, and writes one static page for each. Set `TICKERS_DIR` to read another
directory. A page shows the company, its sector and SIC, and a table for each of
the four groups of metrics with the latest value's percentile in the universe
and the sector, and up to 8 periods. A value that is a sum of filed figures
links to the filing of the figure that ends its period; a ratio shows its
formula instead. Below each metric a list shows every filed figure its values
are made of, with sign, tag, period and a link to its filing. A page of
`synthetic` filings warns that its figures mean nothing. Dollar and share
amounts that are whole millions or thousands are written in them, as the
statements are; others are written in full.
