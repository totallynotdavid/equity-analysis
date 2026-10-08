# Backtest

`eq backtest` measures the model out of sample. It reads the prices and filings
that [`eq run`](CLI.md#eq-run) stored, so run that first with a `--start` early
enough for several years of training. When the stored prices are too short, the
command prints the `--start` to run with.

```bash
uv run eq run --universe universes/demo30.txt --prices synthetic --filings synthetic --start 2008-01-01
uv run eq backtest --universe universes/demo30.txt
```

| Option             | Meaning                                            | Default                        |
| ------------------ | -------------------------------------------------- | ------------------------------ |
| `--universe`       | Ticker list file. Required.                        |                                |
| `--first-oos`      | First test quarter begins on or after this date    | as early as allowed            |
| `--holdout-months` | Months of predictions kept out of the metrics      | 24                             |
| `--final`          | Measure the held-back months instead of the others | off                            |
| `--db`             | SQLite file                                        | [see CLI](CLI.md#the-database) |

## Method

The backtest walks forward, in
[`walkforward.py`](../packages/core/index_core/walkforward.py). It refits the
model every calendar quarter on all labelled snapshots (every fifth trading day)
whose label window closed before the quarter began, then predicts only that
quarter. A label reads 64 days ahead ([Model](MODEL.md#the-label)). Each
prediction comes from a model that never saw the quarter's prices. The first
quarter is the first with 100 such snapshots, about two years, or the first on
or after `--first-oos`. The model settings are the ones `eq run` uses.

The last `--holdout-months` of predictions stay out of the metrics. `--final`
measures only those months. Use it once, before a public claim, because each
look at the holdout makes it a weaker test.

## Output

The metrics are in [`evaluation.py`](../packages/core/index_core/evaluation.py).
A date with fewer than 10 stocks is left out.

- **Mean rank IC**: the Spearman correlation of `prob` with excess return over
  SPY on each date, averaged. The Newey-West t-statistic of that mean uses 13
  lags.
- **Mean AUC per date**: the AUC of `prob` against beating SPY, averaged.
- **Hit rate, score 8 or more**: the share of those stocks that beat SPY, to
  compare with the base rate of all stocks.
- **Excess return, score 10 minus score 1**: the mean excess return of the top
  tenth minus that of the bottom tenth, gross of costs.
- **By score**: the hit rate and mean excess return of each score from 1 to 10.

Every metric has a 95% interval from 2000 bootstrap draws that resample whole
months. Snapshots a week apart share most of their outcome, so the output also
states how many independent 63-day windows the period holds. That is the
effective sample, and it is small.

```text
Walk-forward backtest, demo30 universe, synthetic prices, synthetic filings
Experimental, not validated. Gross of costs.

Period: development, up to 2024-07-07
Out of sample: 2011-04-05 to 2024-07-02, refit every quarter (62 fits, first trained on 106 snapshots up to 2010-12-28)
Rows: 20760 stock-days on 692 snapshot dates (every 5th trading day) in 160 months
Effective sample: about 54 independent 63-day windows. ...

Base rate, share of stocks beating SPY: 47.8%

metric                                    value   95% CI (by month)
mean rank IC                                0.029   [0.002, 0.056]
mean AUC per date                           0.519   [0.497, 0.542]
hit rate, score >= 8 (base rate above)      50.1%   [45.0%, 54.9%]
excess return, score 10 minus score 1       +2.0%   [+0.6%, +3.5%]
Newey-West t-statistic of the mean IC: 1.49

score   rows   hit rate   95% CI           mean excess   95% CI
   10   2076     53.7%   [48.3%, 58.7%]        +1.2%   [-1.7%, +3.9%]
  ...
```

The output above is from fake prices and means nothing. It ends with a caveat:
the ticker list is fixed, so survivors are over-represented.
