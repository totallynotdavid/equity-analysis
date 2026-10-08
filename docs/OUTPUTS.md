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

## API

`index-api` is a read-only FastAPI app
([`packages/api/index_api/main.py`](../packages/api/index_api/main.py)). It
reads the database at `$INDEX_DB`, else `data/index.sqlite`, on every request.

```bash
mise run api   # http://127.0.0.1:8000/docs
```

Without mise:
`uv run uvicorn index_api.main:app --reload --app-dir ./packages/api/`.

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

```bash
mise run web   # http://localhost:4321
cd web && bun run build
```

The build reads `../outputs/scores.json`, relative to `web/`. Set `SCORES_JSON`
to read another file. The page shows the disclaimer, the as-of date and a table
of rank, ticker and score. It warns when `source` is not `tiingo`. Without a
file it says there are no scores yet.
