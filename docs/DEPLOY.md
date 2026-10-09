# Deploy

The page and the API run as one [Vercel](https://vercel.com/) project,
`equity-analysis`, at <https://equity-analysis.vercel.app>.

| URL                                             | Serves                                     |
| ----------------------------------------------- | ------------------------------------------ |
| <https://equity-analysis.vercel.app/>           | The static page from `web/`                |
| <https://equity-analysis.vercel.app/api/scores> | `GET /scores` of [the API](OUTPUTS.md#api) |
| <https://equity-analysis.vercel.app/api/health> | `GET /health` of the API                   |
| <https://equity-analysis.vercel.app/api/docs>   | The interactive API docs                   |

The page and the API share one origin, so the API's CORS list does not apply.
The page loads `/api/scores` in the browser and shows what the API returns
([Outputs](OUTPUTS.md#web-page)).

The deployed scores come from
[`scripts/deploy-data.sh`](../scripts/deploy-data.sh), also
`mise run deploy:data`. It scores
[`universes/demo30.txt`](../universes/demo30.txt) on synthetic prices and
filings, so the page shows the synthetic-data warning and the scores mean
nothing. The script writes `data/deploy.sqlite` and `data/deploy-scores.json`,
apart from the files `eq run` writes by default, and switches the database out
of WAL mode because the function's disk is read-only.

## What runs where

- **Page.** The `buildCommand` in [`vercel.json`](../vercel.json) runs the
  script above, then builds `web/` from `data/deploy-scores.json` into
  `web/dist`.
- **API.** [`api/index.py`](../api/index.py) mounts the FastAPI app of
  [`index-api`](../packages/api/index_api/main.py) under `/api`. Vercel installs
  the Python dependencies named by the root
  [`pyproject.toml`](../pyproject.toml) and bundles `data/deploy.sqlite` with
  the function.

## Redeploy

The Vercel project is connected to the GitHub repository. A push to `master`
builds and deploys production, and every other branch gets a preview. The
`ignoreCommand` in `vercel.json` skips the build when the push changed none of
the page, the API, the packages, the universes, the deploy script or the Vercel
files. A change to the docs alone does not redeploy.

Deploy a checkout by hand:

```bash
vercel deploy --prod --yes
```

The build regenerates the scores on Vercel, so the checkout needs no `data/`.
