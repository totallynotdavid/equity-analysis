# Risk and factor exposure

Every ticker report holds a `risk` field: how much the stock moves, how far it
has fallen, its beta, and what share of its movement the Fama-French factors
explain, each with a 95% interval. The code is in
[`risk.py`](../packages/core/index_core/risk.py). `eq run` and `eq metrics`
compute it with the rest of the [ticker report](FUNDAMENTALS.md#ticker-reports).

## The window

All figures describe the same `WINDOW` of 756 trading days, three years. They
are the last 756 days on which the stock has a return, SPY has a return, and the
stored [factor file](CLI.md#eq-factors) has a row. The file ends a month or two
before the report's date, so the window ends where the file does, not on the
report's date. The report gives the window's first and last day.

A stock with fewer than 756 such days has `status: insufficient_history` and a
`reason` that says how many it has. The page shows "Insufficient history" and
that reason. A database with no factor file gives the same status for every
ticker, with the reason `no factor returns are stored; run eq factors`.

## The figures

| Field          | Meaning                                                                                                                              |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `beta`         | The slope of the stock's daily return on SPY's daily return                                                                          |
| `volatility`   | The standard deviation of the daily return times the square root of 252                                                              |
| `max_drawdown` | The largest fall of a position in the stock from its peak over the window, as a negative fraction. The position starts at 1 on day 1 |
| `loadings[]`   | The coefficients of the daily return above the risk-free rate on `mkt_rf`, `smb`, `hml`, `rmw` and `cma`                             |
| `alpha`        | The intercept of that regression times 252: the part of the return the five factors do not explain, in a year                        |
| `r2`           | The share of the variance of the daily return above the risk-free rate that the five factors explain                                 |

`beta`, `alpha` and each loading is an estimate with `value`, `se`, `lo` and
`hi`. The interval is the value plus or minus 1.96 standard errors.

Returns are daily total returns from the adjusted close, so dividends count.

## The standard errors

Daily stock returns change in volatility and correlate from day to day, which
makes the standard errors of an ordinary regression too small. Both regressions
use Newey-West errors with a Bartlett kernel and `floor(4 (n / 100)^(2/9))`
lags, which is 6 for 756 days, scaled by `n / (n - k)`
([`regression.py`](../packages/core/index_core/regression.py)). The
[backtest](BACKTEST.md#alpha) uses the same regression, with 3 lags, for the
monthly alpha of its strategy.

The errors are asymptotic, and the interval takes them as normal. Over 756 days
that holds up: the tests simulate 400 stocks with volatility that follows the
market and with correlated shocks, and each coefficient's interval covers the
true value in between 91% and 99% of them.

## Stored data

`risk` holds one row per ticker with a fit: the window, `beta`, its standard
error, `volatility`, `max_drawdown` and `r2`. `exposures` holds its `alpha` and
its five loadings with their standard errors. Both are replaced whenever the
metrics are computed. The full report, with the intervals, is in
`ticker_reports`.

## Limits

- A loading describes the window. A company that changed what it does, or the
  size of its debt, has loadings that average the old and the new.
- Six coefficients from three years of daily returns are noisy. The intervals
  show how noisy: a loading whose interval includes zero is not distinguishable
  from zero. The page marks the others in bold.
- An alpha is the return no factor explains, over a window. Its interval is
  often wide enough to include zero, and it is not a forecast.
- Beta against SPY and the market loading differ slightly: the loading is the
  market's effect with the other four factors held fixed. The beta in the
  [valuation](VALUATION.md#the-inputs) is a separate regression over 504 days,
  and is shrunk toward 1.
- The window ends before the report's date. A fall in the weeks since the factor
  file ends is not in the drawdown.
- The factors are the US market's. The loadings of a company with most of its
  business abroad describe it less well.
