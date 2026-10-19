# Backtest

`eq backtest` measures the model out of sample and asks whether a score earns
money after costs. It reads the prices and filings that
[`eq run`](CLI.md#eq-run) stored, so run that first with a `--start` early
enough for several years of training. When the stored prices are too short, the
command prints the `--start` to run with. The alpha table also needs the factors
that [`eq factors`](CLI.md#eq-factors) stores.

```bash
uv run eq run --universe universes/demo30.txt --prices synthetic --filings synthetic --start 2008-01-01
uv run eq factors --source synthetic
uv run eq backtest --universe universes/demo30.txt
```

| Option             | Meaning                                                                                               | Default                        |
| ------------------ | ----------------------------------------------------------------------------------------------------- | ------------------------------ |
| `--universe`       | [Universe file](CLI.md#universe-files). Required.                                                     |                                |
| `--first-oos`      | First test quarter begins on or after this date                                                       | as early as allowed            |
| `--holdout-months` | Months of predictions kept out of the metrics                                                         | 24                             |
| `--final`          | Measure the held-back months instead of the others                                                    | off                            |
| `--cost-bps`       | Loss on each trade's notional, one way, in basis points                                               | 10                             |
| `--predictions`    | CSV file for the measured rows: `date`, `ticker`, `lightgbm`, `ridge`, `composite`, `label`, `excess` | none                           |
| `--db`             | SQLite file                                                                                           | [see CLI](CLI.md#the-database) |

The command opens the database for writing, because every run adds its
specifications to the [ledger](#multiple-testing). A negative cost is an error.

## Method

The backtest walks forward, in
[`walkforward.py`](../packages/core/index_core/walkforward.py). It refits the
models every calendar quarter on all labelled snapshots (every fifth trading
day) whose label window closed before the quarter began, then predicts only that
quarter. A snapshot row exists only for a stock that is a member on that date,
and its features are ranked among that date's members
([universe files](CLI.md#universe-files)). A stock that leaves the universe
keeps its rows up to its end date. A label reads 64 days ahead
([Model](MODEL.md#the-label)). Each prediction comes from models that never saw
the quarter's prices. The first quarter is the first with 100 such snapshots,
about two years, or the first on or after `--first-oos`. The LightGBM settings
are the ones `eq run` uses.

Three models are fitted on the same rows and measured side by side:

- **LightGBM**: the model that `eq run` scores with.
- **ridge**: a linear fit of the label on the 34 ranked features, with a missing
  feature at the middle rank
  ([`baselines.py`](../packages/core/index_core/baselines.py)).
- **composite**: no fit. The mean rank of the value features (`sales_to_ev`,
  `ebitda_to_ev`, `earnings_yield`, `fcf_yield`), the quality features (`roa`,
  `gross_margin`, `op_margin`, `net_margin`, minus `accruals`) and `mom_12_1`,
  each family counting once.

The last `--holdout-months` of predictions stay out of the metrics. `--final`
measures only those months, and chooses its candidate on the others. Use it
once, before a public claim, because each look at the holdout makes it a weaker
test.

## Output

The metrics are in [`evaluation.py`](../packages/core/index_core/evaluation.py).
A date with fewer than 10 stocks is left out.

- **Models**: for each model, the mean rank IC (the Spearman correlation of its
  score with excess return over SPY on each date, averaged), its Newey-West
  t-statistic with 13 lags, the mean AUC per date, and the mean excess return of
  score 10 minus score 1.
- **Portfolios**: see [below](#portfolios).
- **Net decile spread by cost**: the spread at 0, 5, 10, 20 and 50 bps, whatever
  `--cost-bps` is.
- **Spread alpha**: see [below](#alpha).
- **Multiple testing**: see [below](#multiple-testing).
- **Rank IC of each feature alone**: see [below](#each-feature-alone).
- **By score**: for LightGBM, the hit rate and mean excess return of each score
  from 1 to 10.
- **Candidate and verdict**: see [below](#the-verdict).

Every interval is 95% from 2000 bootstrap draws that resample whole months,
except the portfolio intervals ([below](#portfolios)). Snapshots a week apart
share most of their outcome, so the output also states how many independent
63-day windows the period holds. That is the effective sample, and it is small.

```text
Walk-forward backtest, demo30 universe, synthetic prices, synthetic filings, synthetic factors
Experimental. Net of a 10 bps one-way cost, an assumed figure: no source here has bid-ask spreads. The break-even cost is the measured one.

Period: development, up to 2024-06-30
Out of sample: 2011-04-05 to 2024-06-25, refit every quarter (61 fits, first trained on 106 snapshots up to 2010-12-28)
Rows: 20730 stock-days on 691 snapshot dates (every 5th trading day) in 159 months
Effective sample: about 54 independent 63-day windows. ...
Member-years 2008-01-01 to 2026-10-16: 570, 100.0% priced, 100.0% with filings.

Models, on the same rows (95% CI by month)
model       mean rank IC   95% CI                t     AUC   spread
LightGBM          +0.029   [+0.002, +0.055]   1.46   0.518    +2.0%
ridge             +0.059   [+0.028, +0.090]   2.39   0.544    +2.0%
composite         -0.050   [-0.085, -0.017]  -1.81   0.453    -2.3%

Portfolios, rebalanced monthly, 10 bps one way (returns a year; turnover is the share of a leg traded each month)
model       portfolio            gross    net      95% CI (net)          Sharpe  turnover  break-even cost
LightGBM    top decile - all    +11.1%   +10.3%  [+2.1%, +18.4%]        0.65      67%  138.3 bps [37.1, 241.4]
LightGBM    top - bottom         +8.0%    +6.0%  [-4.9%, +17.4%]        0.27     166%   40.1 bps [-14.4, 98.2]
  ...

Multiple testing: the ledger of this database counts 3 specifications
model        monthly Sharpe   chance Sharpe   probability above chance
LightGBM             0.077           0.119                      0.299
  ...

Candidate: ridge. LightGBM does not beat both baselines on rank IC and net spread here, so the ridge baseline is the candidate.
  [ ] net decile-spread interval excludes zero: [-7.8%, +14.7%] a year
  [x] rank IC t-statistic above 2: 2.39
  [ ] Deflated Sharpe probability above 0.95: 0.201
Verdict: none. This is the development period; run with --final once for the held-out months.
```

The output above is from fake prices, fake filings and fake factors and means
nothing. It ends with a caveat. A name that stopped trading has no label for its
last 64 trading days, because its price ends before the window does, so exits by
delisting are under-represented even in a point-in-time universe. With `demo30`
the universe is also today's names, so survivors are over-represented.

The `Member-years` line counts the tickers that were members in each calendar
year of the stored period and gives the shares that have a stored bar in that
year and a filing by its end ([`eq coverage`](FUNDAMENTALS.md#eq-coverage)). A
share below 100% says how much of the universe the table leaves out.

A name that was a member during the stored period but has no stored prices,
because Tiingo does not list it, is left out. The output then has a `Missing:`
line with the count and the first tickers. These are mostly names that were
acquired or delisted, so the rows lean toward survivors even in a point-in-time
universe. A name that left before the first stored day is not counted.

## Portfolios

[`portfolio.py`](../packages/core/index_core/portfolio.py) turns each model's
scores into two portfolios, equally weighted and rebalanced on the first
snapshot of each calendar month, with the trade at the close of the next trading
day:

- **top decile - all**: the return of the stocks of score 10 minus that of the
  equal-weighted universe, as a long-only investor compares with holding all.
- **top - bottom**: long score 10, short score 1.

Each leg is bought and sold at the close after the signal and held to the next
entry. A stock whose prices end keeps its last close and its weight in the leg.
A run needs at least 6 monthly rebalances.

Costs are charged on what trades. The weights drift with returns between
rebalances, and the next rebalance trades back to equal weights. The first
rebalance buys each leg from cash. A leg that is replaced whole trades twice its
capital. The turnover column is the mean amount traded a month, as a share of
one leg. Top - bottom trades both legs; top decile - all trades only the top
leg, because the universe reference is not a position.

- **gross** and **net** are the mean monthly return times 12, before and after
  `--cost-bps` on each trade.
- **95% CI (net)** and the break-even interval resample blocks of three months,
  2000 times.
- **Sharpe** is the net monthly Sharpe ratio times the square root of 12.
- **break-even cost** is the one-way cost that makes the mean net return zero:
  the mean gross return over the mean traded. It does not depend on
  `--cost-bps`. A strategy with no gross edge prints `none: no gross edge`.

`--cost-bps` defaults to 10, an assumed figure: neither Tiingo nor the SEC gives
bid-ask spreads. The break-even cost does not assume one. Compare it with the
cost you expect to pay.

## Alpha

The monthly net return of each model's top - bottom portfolio is regressed on
the monthly Mkt-RF, SMB, HML, RMW and CMA of the Fama-French five-factor daily
file that [`eq factors`](CLI.md#eq-factors) stores. The returns and the factors
cover the same days: daily factors are compounded from each entry to each exit.
The alpha is annualized as 12 times the monthly intercept, with
heteroskedasticity- and autocorrelation-robust standard errors (3 lags). The
table gives the t-statistic, R², the number of months and the loadings.
[`significance.py`](../packages/core/index_core/significance.py)

With no factors stored, the table says `not computed: no factors are stored.`
and to run `eq factors`. With factors stored but fewer than 12 months covered,
it says `not computed: the factor file covers too few of the holding months.`
and does not suggest a command.

## The stored backtest

Each `eq backtest` stores its result in the database, replacing the earlier one
of the same universe and period (`development` or `holdout`): the run's
settings, the three checks and the verdict, the out-of-sample predictions, and a
row of every metric above for each model, year, score and feature. `eq run` and
`eq export` publish it as `evidence.json`, preferring the holdout run
([Outputs](OUTPUTS.md#evidencejson)). The web page `/evidence` prints that file
and computes nothing, so a number on it is a number the backtest stored. The
ledger below is kept apart: it outlives the runs it counts.

## Multiple testing

Each `eq backtest` records a specification for each model in the `trials` table
of the database: a hash of the model, its settings, the universe and its
memberships, the first test quarter, `--holdout-months`, `--final`, and
`--cost-bps`, with the monthly Sharpe ratio of the net top - bottom portfolio. A
`--final` run also records the development specification that chose its
candidate. The stored data is not part of the hash, so the same specification on
more data replaces its row. A repeat of a specification replaces its row. Any
change, including a different cost or `--final`, adds a row.

The Deflated Sharpe Ratio (Bailey and López de Prado) is the probability that
the true Sharpe ratio is above the best that the ledger's count of strategies
with no edge would reach by luck. The chance Sharpe grows with the count and
with the spread of the ledger's Sharpe ratios; it is 0 with one specification.
The probability also allows for the skew and kurtosis of the monthly returns and
for the number of months. Deleting the database resets the count, so keep the
database that a claim rests on.

## Each feature alone

For each of the 34 features, the mean Spearman correlation of the ranked feature
with excess return, date by date, with the same month-block interval and
Newey-West t-statistic as the models. A date counts when at least 10 stocks have
the feature. The table is sorted by the size of t. A new factor needs |t| of 3
or more ([`feature_ic.py`](../packages/core/index_core/feature_ic.py)). Fitting
nothing, it adds nothing to the ledger.

## The verdict

The rule is fixed in [`verdict.py`](../packages/core/index_core/verdict.py) and
printed beside the numbers. The candidate is chosen on the development months,
not on the months that test it. It is LightGBM only if it beats both baselines
there, with a higher mean rank IC and a higher net spread. Otherwise it is the
baseline with the higher net spread. A score may be shown only if, for the
candidate on the held-out months, all three hold:

1. The 95% interval of the net top - bottom return excludes zero.
2. The Newey-West t-statistic of the mean rank IC is above 2.
3. The Deflated Sharpe probability of the net top - bottom portfolio is above
   0.95.

Only `--final` prints a verdict, either `Verdict: show the score.` or
`Verdict: do not show the score.` It lists the development numbers that chose
the candidate above the three checks. The score is LightGBM's, so the verdict
shows it only when LightGBM is the candidate. When a baseline is the candidate
and passes, the verdict is
`Verdict: do not show the score. The <name> baseline passes the checks, but the score is LightGBM's.`
The development period prints the same checks on its own months and
`Verdict: none`, because it chooses and tests on the same months and the
settings were also chosen on them.

Two looks remain. On unchanged data every `--final` run, whatever its cost or
settings, tests the same held-out months; each run adds to the ledger, which
lowers the Deflated Sharpe probability of the next. And the ledger counts only
the specifications run, which understates the choices made while reading
development output. A short holdout can give an interval that excludes zero by
chance alone, which is why the rule needs all three.

A `--final` run measures the development months as well, to choose the
candidate, so the ledger records those specifications too and the stored history
must cover both periods.
