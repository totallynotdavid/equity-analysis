# Outputs

A run reaches readers in three ways: a JSON file, a read-only API and a web
page. All three show the same shape,
[`ScoresReport`](../packages/core/index_core/report.py).

## scores.json

`eq run` and `eq export` write `outputs/scores.json` unless `--out` says
otherwise ([CLI](CLI.md)).

| Field                | Meaning                                                     |
| -------------------- | ----------------------------------------------------------- |
| `status`             | Always `experimental, not validated`                        |
| `as_of`              | The trading day the scores are for                          |
| `universe`           | The universe file's name without its extension              |
| `price_source`       | Where the prices came from: `tiingo` or `synthetic`         |
| `facts_source`       | Where the filings came from: `edgar`, `synthetic` or `null` |
| `horizon_days`       | The label horizon, 63 trading days                          |
| `model.train_rows`   | Rows the model was fitted on                                |
| `model.holdout_rows` | Rows in the later holdout the model was measured on         |
| `model.holdout_auc`  | AUC on that holdout, `null` when it holds only one outcome  |
| `rows[].ticker`      | The ticker                                                  |
| `rows[].rank`        | 1 is the highest probability. Ranks are unique              |
| `rows[].score`       | Decile from 1 to 10. 10 is the top tenth                    |
| `rows[].prob`        | The model's probability of beating SPY over `horizon_days`  |

Rows are ordered by rank. How the score derives from `prob` is in
[Model](MODEL.md#the-score).

The two sources are separate fields, and both are the database's
([CLI](CLI.md#the-database)). A run with `--prices tiingo --filings synthetic`
has `price_source` `tiingo` and `facts_source` `synthetic`. `facts_source` is
`null` when the database holds no filings, for example when the SEC lists none
of the tickers.

## API

`index-api` is a read-only FastAPI app
([`packages/api/index_api/main.py`](../packages/api/index_api/main.py)). It
reads the database at `$INDEX_DB`, else `data/index.sqlite`, on every request
([CLI](CLI.md#the-database)).

```bash
mise run api   # http://127.0.0.1:8000/docs
```

Without mise: `uv run uvicorn index_api.main:app --reload`.

| Endpoint      | Returns                                                                   |
| ------------- | ------------------------------------------------------------------------- |
| `GET /scores` | The latest run of any universe, as in `scores.json`. 404 before a run     |
| `GET /health` | `{"status": "ok", "as_of": "2026-10-08"}`. `as_of` is `null` before a run |

CORS allows `GET` from `http://localhost:4321` and `http://127.0.0.1:4321`, the
web dev server.

## Web page

`web/` is an [Astro](https://astro.build/) site with one page. The build renders
the scores file into the page, so run `eq run` first and build again after each
run. In the browser, the page then fetches `/scores` from the API and renders
that over the build's scores. `PUBLIC_API_URL` sets the API's base URL, `/api`
by default. When the fetch fails, as with `mise run web` and no API, the build's
scores stay.

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
warns. Without a file the page says there are no scores yet.
