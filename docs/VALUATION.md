# Valuation

Every ticker report holds a reverse DCF: not what a stock is worth, but what
growth its price already assumes. The code is in
[`valuation.py`](../packages/core/index_core/valuation.py). `eq run` and
`eq metrics` compute it with the rest of the
[ticker report](FUNDAMENTALS.md#ticker-reports).

A discounted cash flow gives a value for each set of assumptions, and the
assumptions cannot be observed. So the report gives no fair value. It gives the
growth that makes the model worth the price, and a grid of what the model is
worth across discount rates and growth.

## The model

Free cash flow starts at `fcf0` and grows at `g` a year for 10 years. After that
it grows at the terminal growth for ever. All of it is discounted at one rate,
the `wacc`, with the first flow one year out:

```text
EV(g) = sum over t = 1..10 of fcf0 (1+g)^t / (1+wacc)^t
        + fcf0 (1+g)^10 (1+gT) / (wacc - gT) / (1+wacc)^10
```

`EV` rises with `g`, so bisection finds the one `g` at which `EV(g)` is the
market's enterprise value. That is the implied growth. It is searched for
between -50% and 100% a year.

A terminal growth at or above the discount rate makes the terminal value
infinite or negative, so `enterprise_value` and `implied_growth` raise
`ValueError` for it.

## The inputs

| Input               | Where it comes from                                                                                                                                   |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| Free cash flow      | The `free_cash_flow` metric: trailing twelve months of operating cash flow minus capex                                                                |
| `fcf0`              | Free cash flow plus the `interest_expense` metric times one minus the tax rate. The interest is paid to lenders, so this is the flow before financing |
| Tax rate            | The `tax_rate` metric, kept between 0% and 35%. 21% where the company reports none                                                                    |
| Price               | The as-traded close of the report's date                                                                                                              |
| Shares              | The latest cover-page count, scaled for splits since it was filed, as for [the ratios](FUNDAMENTALS.md#the-ratios)                                    |
| Net debt            | The `debt` metric minus the `cash` metric. The market's enterprise value is the price times the shares, plus net debt                                 |
| Beta                | Our own regression: the slope of the stock's daily returns on SPY's over the latest 504 days, at least 252. It is shrunk toward 1 as `2/3 beta + 1/3` |
| Risk-free rate      | The `rf` of the stored [factor file](CLI.md#eq-factors) over its last 21 days, compounded to a year                                                   |
| Equity risk premium | 5.0%. Nothing in the project measures it, so it is an assumption                                                                                      |
| Cost of equity      | Risk-free rate plus beta times the equity risk premium                                                                                                |
| Cost of debt        | Interest expense over debt, kept between the risk-free rate and the cost of equity                                                                    |
| `wacc`              | The cost of equity and the after-tax cost of debt, weighted by market value and debt                                                                  |
| Terminal growth     | 2.5%, or the risk-free rate where that is lower                                                                                                       |

Stock compensation is not deducted from free cash flow. A company that pays its
staff in shares looks cheaper than it is.

## The output

The `valuation` field of a ticker report ([Outputs](OUTPUTS.md#ticker-reports))
has a `status`:

- `ok` with a `model` that holds every input above, the `implied_growth`, the
  `tv_share` (the part of the enterprise value that is terminal value, at the
  implied growth), and the grid.
- `not_meaningful` with a `reason`.

The grid has five rows, the `wacc` and the rates 1 and 2 points on either side,
and seven columns of growth from -5% to 25%. A cell is the model's value of a
share at that rate and growth, which is the enterprise value less net debt over
the shares, and never below zero. Each row also gives the growth that makes the
model worth the price at its rate. The value falls down a column and rises along
a row.

### When it is not meaningful

| Reason                                          | When                                                                                     |
| ----------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `SIC <code> is a financial company: ...`        | The SIC code is from 6000 to 6799: banks, insurers, brokers and real estate              |
| `free cash flow is not positive`                | Trailing free cash flow is zero or negative                                              |
| `missing price, shares, ...`                    | An input is not known. The ticker has no price in the 7 days before the date, or no beta |
| `missing interest expense`                      | The company has debt and reports no interest on it                                       |
| `cash exceeds the market value plus debt`       | The enterprise value is not positive                                                     |
| `the discount rate ... is too near ...`         | The lowest rate of the grid would not be above the terminal growth                       |
| `the price implies growth outside -50% to 100%` | No growth in the search range makes the model worth the price                            |

A company that reports no long-term debt tag has no `debt` metric, so it is
`missing debt` and not a company with none.

## Order of commands

The risk-free rate comes from the factor file, so load it before the metrics:

```bash
uv run eq factors
uv run eq run --universe universes/demo30.txt
```

or, on a database that already has prices and filings:

```bash
uv run eq factors
uv run eq metrics --universe universes/demo30.txt
```

Without the factor file every valuation is `missing risk-free rate`.

## Limits

- The model has one growth rate for ten years and then a fixed terminal one.
  Real cash flows are lumpy, and the terminal value is most of the value for
  most companies. `tv_share` says how much.
- The beta is a regression on two years of daily returns of one stock. Its
  standard error is large. The grid's five discount rates exist for that.
- The equity risk premium and the terminal growth are choices, not measurements.
- Debt is its book value as filed. Operating leases are not in it.
- The factor file ends a month or two before the report's date, so the risk-free
  rate is that old. It is the one-month bill rate, not a ten-year yield, so the
  discount rate of a low-beta company can be near the terminal growth. Those
  companies are `not_meaningful`.
