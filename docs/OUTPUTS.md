# Outputs

A run reaches readers in three ways: a JSON file, a read-only API and a web
page. All three show the same shape,
[`ScoresReport`](../packages/core/index_core/report.py).

## scores.json

`eq run` and `eq export` write `outputs/scores.json` unless `--out` says
otherwise ([CLI](CLI.md)).

| Field                | Meaning                                                    |
| -------------------- | ---------------------------------------------------------- |
| `status`             | Always `experimental, not validated`                       |
| `as_of`              | The trading day the scores are for                         |
| `universe`           | The ticker list's file name without its extension          |
| `source`             | The price source: `tiingo` or `synthetic`                  |
| `horizon_days`       | The label horizon, 63 trading days                         |
| `model.train_rows`   | Rows the model was fitted on                               |
| `model.holdout_rows` | Rows in the later holdout the model was measured on        |
| `model.holdout_auc`  | AUC on that holdout, `null` when it holds only one outcome |
| `rows[].ticker`      | The ticker                                                 |
| `rows[].rank`        | 1 is the highest probability. Ranks are unique             |
| `rows[].score`       | Decile from 1 to 10. 10 is the top tenth                   |
| `rows[].prob`        | The model's probability of beating SPY over `horizon_days` |

Rows are ordered by rank. How the score derives from `prob` is in
[Model](MODEL.md#the-score).

`source` records the price source only, not the filings source. A run with
`--prices tiingo --filings synthetic` has `source` `tiingo`.

## API

`index-api` is a read-only FastAPI app
([`packages/api/index_api/main.py`](../packages/api/index_api/main.py)). It
reads the database at `$INDEX_DB`, else `data/index.sqlite`, on every request.
To serve a synthetic run, set `INDEX_DB=data/synthetic-synthetic.sqlite`
([CLI](CLI.md#the-database)).

```bash
mise run api   # http://127.0.0.1:8000/docs
```

Without mise:
`uv run uvicorn index_api.main:app --reload --app-dir ./packages/api/`. The
`--app-dir` is required: the `index-api` package has no build metadata, so
`index_api` is not importable without it.

| Endpoint      | Returns                                                                   |
| ------------- | ------------------------------------------------------------------------- |
| `GET /scores` | The latest run of any universe, as in `scores.json`. 404 before a run     |
| `GET /health` | `{"status": "ok", "as_of": "2026-10-08"}`. `as_of` is `null` before a run |

CORS allows `GET` from `http://localhost:4321` and `http://127.0.0.1:4321`, the
web dev server.

## Web page

`web/` is an [Astro](https://astro.build/) site with one page. It reads the
scores file at build time, not in the browser, so run `eq run` first and build
again after each run.

From the repository root:

```bash
(cd web && bun install)   # once
mise run web              # dev server at http://localhost:4321
(cd web && bun run build) # writes web/dist
```

`mise run web` starts `astro dev --host`
([`web/package.json`](../web/package.json)), so the dev server listens on every
network interface of the machine, not only on `localhost`.

`mise run site` runs `eq export` on the default database, then the build.

The build reads `../outputs/scores.json`, relative to `web/`. Set `SCORES_JSON`
to read another file. The page shows the disclaimer, the as-of date and a table
of rank, ticker and score. It warns when `source` is not `tiingo`. Because
`source` names only the price source, a run with
`--prices tiingo --filings synthetic` shows no warning although its fundamentals
are fake. Without a file the page says there are no scores yet.
