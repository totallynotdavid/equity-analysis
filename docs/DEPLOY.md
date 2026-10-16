# Deploy

The page and the API run as one [Vercel](https://vercel.com/) project,
`equity-analysis`, at <https://equity-analysis.vercel.app>.

| URL                                                   | Serves                                     |
| ----------------------------------------------------- | ------------------------------------------ |
| <https://equity-analysis.vercel.app/>                 | The static page from `web/`                |
| <https://equity-analysis.vercel.app/t/AAPL>           | The page of a ticker from `web/`           |
| <https://equity-analysis.vercel.app/api/scores>       | `GET /scores` of [the API](OUTPUTS.md#api) |
| `https://equity-analysis.vercel.app/api/tickers/AAPL` | `GET /tickers/{ticker}` of the API         |
| <https://equity-analysis.vercel.app/api/health>       | `GET /health` of the API                   |
| <https://equity-analysis.vercel.app/api/docs>         | The interactive API docs                   |

The page and the API share one origin, so the API's CORS list does not apply.
The page loads `/api/scores` in the browser and shows what the API returns
([Outputs](OUTPUTS.md#web-page)).

The deployed scores are the committed file
[`deploy/scores.json`](../deploy/scores.json): the demo universe
[`universes/demo30.txt`](../universes/demo30.txt) scored on Tiingo prices and
EDGAR filings. It holds the scores, the as-of date and the two source names, and
no price bars. Vercel has no Tiingo key and a real run takes too long for a
build, so the build never scores. It publishes this file.

The ticker pages come from the committed directory `deploy/tickers`, one
[ticker report](OUTPUTS.md#ticker-reports) per ticker, written by the same run.

## Refresh the scores

[`scripts/deploy-data.sh`](../scripts/deploy-data.sh), also
`mise run deploy:data`, runs `eq run` on the demo universe and writes
`deploy/scores.json` and `deploy/tickers`. It needs the two variables of
[Real data](CLI.md#real-data):

```bash
TIINGO_API_KEY=... \
SEC_USER_AGENT='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36' \
  mise run deploy:data
```

Arguments after the task go to `eq run`, for example `-- --end 2026-10-10`. The
script keeps its database in `data/real.sqlite` and the responses of Tiingo and
the SEC in `data/cache`, both ignored by git, so a repeat run on the same day
costs no requests. With an empty cache the Tiingo pause sets the run time
([Real data](CLI.md#real-data)). A Tiingo rate limit or an SEC block stops the
run with the source's message and leaves `deploy/scores.json` as it was. Run the
task again to carry on from the cache.

Commit the new `deploy/scores.json` and `deploy/tickers` and push. A ticker
without prices or filings is left out of the scores and named in the file's
`left_out`, which the page lists ([Outputs](OUTPUTS.md#web-page)).

## What runs where

- **Page.** The `buildCommand` in [`vercel.json`](../vercel.json) runs
  [`scripts/deploy-db.sh`](../scripts/deploy-db.sh), then builds `web/` from
  `deploy/scores.json` and `deploy/tickers` into `web/dist` with `SCORES_JSON`
  and `TICKERS_DIR`.
- **API.** [`api/index.py`](../api/index.py) mounts the FastAPI app of
  [`index-api`](../packages/api/index_api/main.py) under `/api`. Vercel installs
  the Python dependencies named by the root
  [`pyproject.toml`](../pyproject.toml) and bundles `data/deploy.sqlite` with
  the function. `deploy-db.sh` makes that file with
  [`eq import`](CLI.md#eq-import) from `deploy/scores.json` and
  `deploy/tickers`, and switches it out of WAL mode because the function's disk
  is read-only.
- **Guard.** `deploy-db.sh` fails unless `price_source` is `tiingo` and
  `facts_source` is `edgar`, and unless every ticker report has `edgar` filings,
  so synthetic data cannot reach the live page. It also fails when a ticker
  report is for another universe or as-of date than `deploy/scores.json`
  ([`eq import`](CLI.md#eq-import)). The synthetic path stays for tests and the
  local demo ([README](../README.md)).

## Redeploy

The Vercel project is connected to the GitHub repository. A push to `master`
builds and deploys production, and every other branch gets a preview. The
`ignoreCommand` in `vercel.json` skips the build when the push changed none of
the page, the API, the packages, `deploy/`, the deploy scripts or the Vercel
files. A change to the docs alone does not redeploy.

Deploy a checkout by hand:

```bash
vercel deploy --prod --yes
```

The build reads the committed `deploy/scores.json`, so the checkout needs no
`data/`.
