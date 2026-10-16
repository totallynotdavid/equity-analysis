# Fundamentals

The 14 fundamental features come from the SEC's XBRL `companyfacts` data. The
code is in [`sources/edgar.py`](../packages/core/index_core/sources/edgar.py)
(fetching and parsing) and
[`features/fundamental.py`](../packages/core/index_core/features/fundamental.py)
(features).

## What is read

[`EdgarSource`](../packages/core/index_core/sources/edgar.py) maps a ticker to
its company with the CIK in the universe's companies file
([CLI](CLI.md#universe-files)), else with the SEC's ticker list, then fetches
that company's `companyfacts` document, and those of its earlier registrants. It
keeps facts from forms 10-K and 10-Q, their amendments and their transition
reports, for 19 concepts. Each fact keeps the accession number (`accn`), form,
fiscal year (`fy`), fiscal period (`fp`) and registrant (`cik`) of the filing
that reported it.

| Kind          | Concepts                                                                                                                                                        |
| ------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Period flows  | revenue, gross profit, operating income, net income, pre-tax income, income tax, interest expense, stock compensation, depreciation, operating cash flow, capex |
| Per share     | diluted EPS, diluted weighted-average shares                                                                                                                    |
| Balance sheet | total assets, long-term debt, current debt, equity, cash                                                                                                        |
| Cover page    | shares outstanding (summed over share classes)                                                                                                                  |

Companies change XBRL tags over time, so each concept lists several tags in
priority order. A period takes the first tag that reports it. Capital spending
and interest expense are read as positive amounts, because filers differ on
their sign. Debt is long-term debt plus the current portion reported for the
same date. The tag lists are `CONCEPTS` in
[`sources/edgar.py`](../packages/core/index_core/sources/edgar.py).

The same module reads the company behind a ticker from the SEC's submissions
API: its name, CIK, SIC code and the SIC title. The GICS sector is not in EDGAR.
It comes from the universe's companies file.

## When a fact is known

Every fact carries the date it was filed.

- A fact is used from the first trading day after that date, never on it,
  because a filing can land after the close.
- A later filing that restates a period replaces the earlier value from its own
  filing date on, never before it.
- Nothing is filled backward. Before a company's first filing its fundamentals
  are missing. A name with no filings keeps its technical features in
  `eq backtest`; `eq run` leaves it out ([CLI](CLI.md#eq-run)).
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

## Ticker reports

A ticker report is what the page of a ticker shows
([Outputs](OUTPUTS.md#ticker-reports)). For each of the 21 metrics in
[`metrics.py`](../packages/core/index_core/metrics.py) it holds the latest
twelve points, oldest first, and the percentile of the latest value.

| Group            | Metrics                                                                                                                        |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| Income statement | revenue, gross profit, operating income, interest expense, pre-tax income, income tax, net income, diluted EPS, diluted shares |
| Cash flow        | stock compensation, free cash flow (operating cash flow minus capex)                                                           |
| Balance sheet    | total assets, cash, debt, net debt (debt minus cash), equity, shares outstanding                                               |
| Ratios           | gross margin, operating margin, net margin, tax rate (income tax over pre-tax income)                                          |

- A flow is a [trailing twelve-month](#trailing-twelve-months) figure at each
  fiscal period end. Diluted shares are the weighted average of the longest
  period reported to that date. A balance sheet quantity is its value at the
  date.
- A point lists the filed figures it is made of: each with its tag, period,
  value and sign, and the filing that first reported that value (accession
  number, form, fiscal year and period, filing date and the EDGAR page that
  lists the filing's documents). A TTM adds the latest fiscal year to the
  year-to-date period and subtracts the prior year's period, so it has three
  figures. The point's value is their signed sum, or the stated formula of a
  derived metric over its figures.
- A point uses only filings made before the report's as-of date. A restated
  figure is the latest filed value, and links to the first filing that reported
  it.
- `pct_universe` is the percent of the universe's members, on the same day, at
  or below the value, so tied members share the higher percent. `pct_sector` is
  the same among members with the same sector in the companies file. A group of
  fewer than 5 values, and a ticker without a sector, has no percentile
  (`null`). Only the percentile of the latest value is in the report. The stored
  daily series, 260 dates per member, stay in the `metrics` table of the local
  database.
- A metric with no point, because a company does not report the concept, is not
  in the report. Interest expense and stock compensation are absent for
  companies that do not tag them.

## eq coverage

```bash
uv run eq coverage --universe universes/demo30.txt
```

`eq coverage` reads the stored filings, so run [`eq run`](CLI.md#eq-run) or
[`eq filings`](CLI.md#eq-filings) first. It counts, for each member at the
latest stored SPY date (or `--as-of`), how many of the 19 concepts have a fresh
value then. It lists the tickers with fewer than `--min-concepts` (default 10)
and why: the concepts they lack, or that the filings source has no company for.
It then counts the names with a fresh value for each concept, and for each tag
of each concept the names that report it fresh and ever. A tag that no name
reports freshly is marked `DEAD`: it is listed, not left out. `--db` selects the
database ([CLI](CLI.md#the-database)).

```text
Fundamentals coverage, toy universe, synthetic filings, as of 2026-09-30
8 of 8 names have at least 10 of 19 concepts with a fresh filed value.
No name has fewer.
Names with a fresh value, by concept (of 8):
  revenue: 8
  ...
Names reporting each tag, fresh now and ever filed (a concept takes the first tag that has the period):
  revenue
    Revenues: 8 fresh, 8 ever
    RevenueFromContractWithCustomerExcludingAssessedTax: 0 fresh, 0 ever  DEAD
    ...
18 dead tags of 37.
```

The synthetic filings use one tag per concept, so the other tags are dead.

A tag is dead for the universe given, not for every company: a tag that only a
bank or an insurer uses is dead in a list of industrial companies. A missing
value stays missing, and the model reads it as missing.
