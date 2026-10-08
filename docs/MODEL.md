# Model

One [LightGBM](https://lightgbm.readthedocs.io/) classifier is fitted on a
chronological, purged split. It predicts whether a stock beats SPY. The code is
in [`model.py`](../packages/core/index_core/model.py),
[`labels.py`](../packages/core/index_core/labels.py) and
[`scoring.py`](../packages/core/index_core/scoring.py).

## The label

A stock's label on day `t` is 1 if its return beat SPY's, else 0. Both returns
run in adjusted prices from the close of day `t + 1` to the close of day
`t + 64`, counted in SPY trading days. A signal built from the close of day `t`
is traded at the close of day `t + 1`, so the label starts there. The last 64
days have no label.

## Features

Every feature at day `t` reads data available on day `t` only. Each is then
replaced by its percentile rank among the tickers of the same day
([`normalize.py`](../packages/core/index_core/features/normalize.py)), so a
feature's market-wide level does not matter.

The 20 technical features
([`technical.py`](../packages/core/index_core/features/technical.py)) come from
daily bars:

| Feature               | Meaning                                                 |
| --------------------- | ------------------------------------------------------- |
| `ret_5`, `ret_21`     | Return over 5 and 21 days                               |
| `ret_63`, `ret_126`   | Return over 63 and 126 days                             |
| `mom_12_1`            | Return from 252 days ago to 21 days ago                 |
| `dist_high_252`       | Close over the 252-day high, minus 1                    |
| `dist_low_252`        | Close over the 252-day low, minus 1                     |
| `vol_21`, `vol_63`    | Standard deviation of daily returns over 21 and 63 days |
| `beta_63`             | 63-day beta to SPY                                      |
| `rsi_14`              | 14-day relative strength index                          |
| `macd_hist`           | MACD (12, 26) minus its 9-day signal, over the close    |
| `bb_pctb`, `bb_width` | Position in, and width of, the 20-day Bollinger bands   |
| `atr_pct`             | 14-day average true range over the close                |
| `adx_14`              | 14-day average directional index                        |
| `stoch_k`             | Position in the 14-day high-low range                   |
| `volume_ratio`        | 21-day mean volume over 63-day mean volume              |
| `log_dollar_volume`   | Log of the 21-day mean of close times volume, as traded |
| `max_drawdown_126`    | Largest fall from a peak in the last 126 days           |

The 14 fundamental features come from SEC filings. See
[Fundamentals](FUNDAMENTALS.md).

## Fit

- **Snapshots.** The model sees every fifth trading day, because rows of one
  stock on neighbouring days are nearly identical.
- **Rows.** A row with no label or a missing technical feature is dropped. A
  missing fundamental stays missing, and LightGBM reads it as missing.
- **Split.** The latest 20% of snapshot dates, at least 5, are the holdout. The
  model is measured on them. Training dates whose label window reaches the first
  holdout day are dropped, so no training label reads a price the holdout has
  seen.
- **Settings.** 150 boosting rounds with the fixed `PARAMS` in `model.py`: small
  trees (5 leaves, depth 3), a learning rate of 0.05 and L2 of 10. The seed is
  fixed and nothing tunes them.
- **Minimum.** Fewer than 20 labelled snapshot dates is an error.

`model.holdout_auc` in `scores.json` is the AUC on that holdout. It is the only
quality figure a run stores. [`eq backtest`](BACKTEST.md) measures the model
walk-forward.

## The score

The model predicts `prob`, the probability of beating SPY, for every ticker on
the latest date. A ticker with a missing technical feature on that date stops
the run, because it has too little history.

Tickers are ranked by `prob`, highest first, with ties broken by ticker. Rank 1
is the highest. The score is `10 - (rank - 1) * 10 // count`, so score 10 is the
top tenth. With 30 tickers, each score has 3.
