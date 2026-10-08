# index

index ranks US stocks with a daily score from 1 to 10. The score is the decile
of a LightGBM model's estimated probability that a stock beats SPY over the next
63 trading days. Score 10 is the top tenth of the stocks you give it.

You give it a list of tickers. It fetches prices and SEC filings, fits the model
and writes the scores to SQLite and to a JSON file. A read-only API and a static
web page show them.

**Experimental, not validated.** The model is fitted once on a chronological,
purged split of about 20 technical and 14 fundamental features. `eq backtest`
measures it walk-forward, but the ticker list is a fixed list of today's names
with no point-in-time membership, so the numbers carry survivorship bias.
Nothing here is investment advice or a recommendation to buy or sell any
security, and you can lose money.

```json
{
  "status": "experimental, not validated",
  "as_of": "2026-10-08",
  "universe": "demo30",
  "source": "synthetic",
  "horizon_days": 63,
  "model": { "train_rows": 5640, "holdout_rows": 1500, "holdout_auc": 0.515 },
  "rows": [
    { "ticker": "MA", "rank": 1, "score": 10, "prob": 0.637 },
    { "ticker": "JNJ", "rank": 2, "score": 10, "prob": 0.626 }
  ]
}
```

The example is `outputs/scores.json` from the synthetic run below, with the rows
cut to two. Synthetic prices are fake, so these scores mean nothing.

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
uv run eq run --universe universes/demo30.txt --source synthetic
```

With real data, set `TIINGO_API_KEY` and `SEC_USER_AGENT` and leave out
`--source`:

```bash
TIINGO_API_KEY=... SEC_USER_AGENT="Jane Doe jane@example.com" \
  uv run eq run --universe universes/demo30.txt
```

[docs/CLI.md](docs/CLI.md) explains the options, the keys and the database.

## Features

- Scores a ticker list as of its latest trading day and stores each run in
  SQLite, one per universe and date. [`eq run`](docs/CLI.md)
- 20 technical features from daily prices and 14 fundamental ratios from SEC
  XBRL filings. A filing counts from the trading day after it was filed.
  [Model](docs/MODEL.md), [fundamentals](docs/FUNDAMENTALS.md)
- Walk-forward backtest with a rank IC, a Newey-West t-statistic, hit rates by
  score and 95% intervals that resample whole months. A final holdout stays out
  of the development metrics. [`eq backtest`](docs/BACKTEST.md)
- Fundamentals coverage report for a ticker list.
  [`eq coverage`](docs/FUNDAMENTALS.md)
- `scores.json` export, a read-only FastAPI service and an Astro page.
  [Outputs](docs/OUTPUTS.md)
- Deterministic synthetic prices and filings for offline runs and tests.

## Documentation

- [The manual](docs/README.md) lists every document: commands, model,
  fundamentals, backtest, outputs and architecture.
- [Contributing](.github/CONTRIBUTING.md) covers setup, checks and tests.
