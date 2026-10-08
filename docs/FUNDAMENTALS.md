# Fundamentals

The 14 fundamental features come from the SEC's XBRL `companyfacts` data. The
code is in [`sources/edgar.py`](../packages/core/index_core/sources/edgar.py)
(fetching and parsing) and
[`features/fundamental.py`](../packages/core/index_core/features/fundamental.py)
(features).

## What is read

[`EdgarSource`](../packages/core/index_core/sources/edgar.py) maps a ticker to
its company with the SEC's ticker list, then fetches that company's
`companyfacts` document. It keeps facts from forms 10-K and 10-Q, their
amendments and their transition reports, for 13 concepts:

| Kind          | Concepts                                                                                      |
| ------------- | --------------------------------------------------------------------------------------------- |
| Period flows  | revenue, gross profit, operating income, net income, depreciation, operating cash flow, capex |
| Balance sheet | total assets, long-term debt, current debt, equity, cash                                      |
| Cover page    | shares outstanding (summed over share classes)                                                |

Companies change XBRL tags over time, so each concept lists several tags in
priority order. A period takes the first tag that reports it. Capital spending
is read as a positive amount paid, because filers differ on its sign. Debt is
long-term debt plus the current portion reported for the same date.

## When a fact is known

Every fact carries the date it was filed.

- A fact is used from the first trading day after that date, never on it,
  because a filing can land after the close.
- A later filing that restates a period replaces the earlier value from its own
  filing date on, never before it.
- Nothing is filled backward. Before a company's first filing its fundamentals
  are missing, and a name with no filings keeps its technical features.
- A value is missing once its period ended more than 460 days before the date,
  so a company that stops filing is not scored on old numbers.

## Trailing twelve months

A flow is a trailing twelve-month (TTM) figure. It adds the latest fiscal year
to the year-to-date period of the current year and subtracts the year-to-date
period of the year before. The TTM a year earlier gives the growth rates. A
piece that is missing leaves the TTM missing.

## The ratios

Market value is the filed share count times the as-traded close of that day,
with the count corrected for splits since it was filed. Enterprise value is
market value plus debt minus cash. A ratio with a non-positive denominator is
missing. Like the technical features, each ratio is then ranked within its date.

| Feature           | Definition                                                    |
| ----------------- | ------------------------------------------------------------- |
| `sales_to_ev`     | TTM revenue over enterprise value                             |
| `ebitda_to_ev`    | TTM operating income plus depreciation, over enterprise value |
| `earnings_yield`  | TTM net income over market value                              |
| `fcf_yield`       | TTM operating cash flow minus capex, over market value        |
| `roa`             | TTM net income over total assets                              |
| `gross_margin`    | TTM gross profit over TTM revenue                             |
| `op_margin`       | TTM operating income over TTM revenue                         |
| `net_margin`      | TTM net income over TTM revenue                               |
| `revenue_growth`  | TTM revenue over the TTM a year earlier, minus 1              |
| `earnings_growth` | Change in TTM net income over the absolute TTM a year earlier |
| `debt_to_equity`  | Debt over equity                                              |
| `accruals`        | TTM net income minus operating cash flow, over total assets   |
| `dilution`        | Shares over the split-adjusted shares a year earlier, minus 1 |
| `asset_growth`    | Total assets over total assets a year earlier, minus 1        |

Yields stand in for price multiples. They stay defined, and keep their order,
when earnings or EBITDA are not positive.

## eq coverage

```bash
uv run eq coverage --universe universes/demo30.txt --db data/synthetic-synthetic.sqlite
```

`eq coverage` reads the stored filings, so run [`eq run`](CLI.md#eq-run) first.
It counts, for each ticker, how many of the 13 concepts have a fresh value at
the latest stored SPY date. It lists the tickers with fewer than
`--min-concepts` (default 10), so a name that a ticker change or an unusual
taxonomy leaves bare is visible. `--db` selects the database
([CLI](CLI.md#the-database)).

```text
Fundamentals coverage, demo30 universe, synthetic filings, as of 2026-10-08
30 of 30 names have at least 10 of 13 concepts with a fresh filed value.
No name has fewer.
```

A missing value stays missing, and the model reads it as missing.
