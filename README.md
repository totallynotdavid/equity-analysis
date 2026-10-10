# index

index ranks US stocks with a daily score from 1 to 10. The score is the decile
of a LightGBM model's estimated probability that a stock beats SPY over the next
63 trading days. Score 10 is the top tenth of the stocks you give it.

You give it a universe: tickers with the dates they were members. It fetches
prices and SEC filings, fits the model and writes the scores to SQLite and to a
JSON file. A read-only API and a static web page show them.

**Experimental, not validated.** The model is fitted once on a chronological,
purged split of 20 technical and 14 fundamental features. `eq backtest` measures
it walk-forward. A universe file gives each name a start and end date, and a
name counts only while it is a member.
[`universes/sp500.txt`](universes/sp500.txt) holds the S&P 500 members from 2011
as Wikipedia's changes table records them, with the dropped names included. The
demo list `demo30` is today's names, so its numbers carry survivorship bias.
Nothing here is investment advice or a recommendation to buy or sell any
security, and you can lose money.

## Get started

You need [uv](https://docs.astral.sh/uv/). uv installs the Python 3.14 that the
packages need. LightGBM needs the OpenMP runtime:

```bash
sudo apt-get install libgomp1   # Debian and Ubuntu
brew install libomp             # macOS (not verified)
```

Install the workspace once:

```bash
git clone https://github.com/totallynotdavid/equity-analysis
cd equity-analysis
uv sync --all-packages
```

Run it on synthetic prices and filings. It needs no keys and takes seconds:

```bash
uv run eq run --universe universes/demo30.txt --prices synthetic --filings synthetic
```

It prints `wrote 30 scores as of <date> to outputs/scores.json`:

```json
{
  "status": "experimental, not validated",
  "as_of": "2026-10-08",
  "universe": "demo30",
  "price_source": "synthetic",
  "facts_source": "synthetic",
  "horizon_days": 63,
  "model": { "train_rows": 5640, "holdout_rows": 1500, "holdout_auc": 0.515 },
  "rows": [
    { "ticker": "MA", "rank": 1, "score": 10, "prob": 0.637 },
    { "ticker": "JNJ", "rank": 2, "score": 10, "prob": 0.626 }
  ]
}
```

The example cuts the rows to two. Synthetic prices are fake, so these scores
mean nothing.

With real data, set `TIINGO_API_KEY` and `SEC_USER_AGENT` and leave out
`--prices` and `--filings`. The synthetic run filled `data/index.sqlite`, which
rejects real data ([the database](docs/CLI.md#the-database)), so point
`INDEX_DB` at a new file. Every later `eq` command then reads that file too:

```bash
export INDEX_DB=data/real.sqlite
TIINGO_API_KEY=... SEC_USER_AGENT="Jane Doe jane@example.com" \
  uv run eq run --universe universes/demo30.txt
```

[docs/CLI.md](docs/CLI.md) explains the options, the keys and the database.

## Features

- Scores a universe as of its latest trading day and stores each run in SQLite,
  one per universe and date. [`eq run`](docs/CLI.md)
- 20 technical features from daily prices and 14 fundamental ratios from SEC
  XBRL filings. A filing counts from the trading day after it was filed.
  [Model](docs/MODEL.md), [fundamentals](docs/FUNDAMENTALS.md)
- Walk-forward backtest with a rank IC, a Newey-West t-statistic, hit rates by
  score and 95% intervals that resample whole months. A final holdout stays out
  of the development metrics. [`eq backtest`](docs/BACKTEST.md)
- Fundamentals coverage report for a universe.
  [`eq coverage`](docs/FUNDAMENTALS.md#eq-coverage)
- `scores.json` export, a read-only FastAPI service and an Astro page.
  [Outputs](docs/OUTPUTS.md)
- Deterministic synthetic prices and filings for offline runs and tests.

## Documentation

- [The manual](docs/README.md) lists every document: commands, outputs, model,
  fundamentals, backtest, architecture and deploy.
- [Live page](https://equity-analysis.vercel.app) shows synthetic demo scores,
  and `/api/scores` returns them as JSON. [Deploy](docs/DEPLOY.md) explains how.
- [Contributing](.github/CONTRIBUTING.md) covers setup, checks and tests.
