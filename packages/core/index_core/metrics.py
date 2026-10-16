"""The fundamentals a ticker page shows, and where each stands among its peers.

`METRICS` is the one list of them. `daily_metrics` evaluates it on every
(date, ticker) of `fundamental_inputs`, the numbers the model reads, and ranks
each value in its universe and in its sector. `statements.py` evaluates the same
list on the filed figures, so a page shows the figure that was ranked.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import numpy as np
import pandas as pd


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from index_core.universe import Universe

# Keep one year of daily metrics so a page can show every reported period.
DAYS = 260
# Do not report a percentile for a group this small because its rank is weak evidence.
MIN_PEERS = 5


@dataclass(frozen=True)
class Metric:
    """A quantity of a company, from one or more `inputs`.

    An input is a quantity the filings report: a column of `fundamental_inputs`.
    A metric with no `derive` is its single input. Otherwise `derive` takes the
    inputs, as numbers or arrays, and `formula` says what it computes.
    """

    group: str
    label: str
    unit: str
    inputs: tuple[str, ...]
    formula: str | None = None
    derive: Callable[..., Any] | None = None


def _difference(left: Any, right: Any) -> Any:
    return np.asarray(left, dtype=float) - np.asarray(right, dtype=float)


def _ratio(top: Any, bottom: Any) -> Any:
    """`top / bottom`, missing where `bottom` is not positive."""
    divisor = np.asarray(bottom, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(divisor > 0, np.asarray(top, dtype=float) / divisor, np.nan)


INCOME = "Income statement"
CASH_FLOW = "Cash flow"
BALANCE = "Balance sheet"
RATIOS = "Ratios"

METRICS: dict[str, Metric] = {
    "revenue": Metric(INCOME, "Revenue", "USD", ("revenue",)),
    "gross_profit": Metric(INCOME, "Gross profit", "USD", ("gross_profit",)),
    "operating_income": Metric(
        INCOME, "Operating income", "USD", ("operating_income",)
    ),
    "interest_expense": Metric(
        INCOME, "Interest expense", "USD", ("interest_expense",)
    ),
    "pretax_income": Metric(INCOME, "Pre-tax income", "USD", ("pretax_income",)),
    "income_tax": Metric(INCOME, "Income tax", "USD", ("income_tax",)),
    "net_income": Metric(INCOME, "Net income", "USD", ("net_income",)),
    "eps_diluted": Metric(INCOME, "Diluted EPS", "USD/share", ("eps_diluted",)),
    "diluted_shares": Metric(INCOME, "Diluted shares", "shares", ("diluted_shares",)),
    "stock_compensation": Metric(
        CASH_FLOW, "Stock compensation", "USD", ("stock_compensation",)
    ),
    "free_cash_flow": Metric(
        CASH_FLOW,
        "Free cash flow",
        "USD",
        ("operating_cash_flow", "capex"),
        "operating cash flow - capital expenditure",
        _difference,
    ),
    "total_assets": Metric(BALANCE, "Total assets", "USD", ("total_assets",)),
    "cash": Metric(BALANCE, "Cash", "USD", ("cash",)),
    "debt": Metric(BALANCE, "Debt", "USD", ("debt",)),
    "net_debt": Metric(
        BALANCE, "Net debt", "USD", ("debt", "cash"), "debt - cash", _difference
    ),
    "equity": Metric(BALANCE, "Equity", "USD", ("equity",)),
    "shares_outstanding": Metric(BALANCE, "Shares outstanding", "shares", ("shares",)),
    "gross_margin": Metric(
        RATIOS,
        "Gross margin",
        "ratio",
        ("gross_profit", "revenue"),
        "gross profit / revenue",
        _ratio,
    ),
    "op_margin": Metric(
        RATIOS,
        "Operating margin",
        "ratio",
        ("operating_income", "revenue"),
        "operating income / revenue",
        _ratio,
    ),
    "net_margin": Metric(
        RATIOS,
        "Net margin",
        "ratio",
        ("net_income", "revenue"),
        "net income / revenue",
        _ratio,
    ),
    "tax_rate": Metric(
        RATIOS,
        "Tax rate",
        "ratio",
        ("income_tax", "pretax_income"),
        "income tax / pre-tax income",
        _ratio,
    ),
}


def optional_float(value: Any) -> float | None:
    """`value` as a float, or None where it is missing."""
    return None if pd.isna(value) else float(value)


def evaluate(metric: Metric, values: list[Any]) -> Any:
    """The metric from its inputs, in the order of `metric.inputs`."""
    if metric.derive is None:
        return values[0]
    return metric.derive(*values)


def daily_metrics(
    inputs: pd.DataFrame,
    universe: Universe,
    sectors: Mapping[str, str],
    days: int = DAYS,
) -> pd.DataFrame:
    """The `METRICS` of the members of `universe` on the last `days` dates of
    `inputs`, with the percentile of each value among the members of its date
    and among those in its sector.

    `inputs` comes from `fundamental_inputs`. A percentile is the percent of the
    group at or below the value, and is missing for a group under `MIN_PEERS`
    values. A ticker without a sector has no sector percentile. The result has
    `ticker`, `as_of`, `metric`, `value`, `pct_universe` and `pct_sector`, and
    no row for a missing value.
    """
    dates = inputs.index.get_level_values("date")
    recent = dates >= dates.unique().sort_values()[-days:][0]
    members = inputs[recent & universe.mask(inputs.index)]

    values = pd.DataFrame(
        {
            name: np.asarray(
                evaluate(metric, [members[i].to_numpy() for i in metric.inputs]),
                dtype=float,
            )
            for name, metric in METRICS.items()
        },
        index=members.index,
    ).replace([np.inf, -np.inf], np.nan)

    by_date = values.groupby(level="date")
    sector = pd.Series(members.index.get_level_values("ticker"), index=members.index)
    sector = sector.map(sectors)
    classified = values[sector.notna().to_numpy()]
    by_sector = classified.groupby(
        [classified.index.get_level_values("date"), sector.dropna().to_numpy()]
    )
    percentiles = {
        "pct_universe": _percent(by_date),
        "pct_sector": _percent(by_sector).reindex(values.index),
    }

    values.columns.name = "metric"
    long = cast("pd.Series", values.stack()).dropna().rename("value").to_frame()
    for name, wide in percentiles.items():
        wide.columns.name = "metric"
        long[name] = cast("pd.Series", wide.stack())
    return (
        long.reset_index()
        .rename(columns={"date": "as_of"})
        .loc[:, ["ticker", "as_of", "metric", "value", "pct_universe", "pct_sector"]]
    )


def _percent(groups: Any) -> pd.DataFrame:
    """Percent of each group at or below a value, missing for a small group."""
    ranks: pd.DataFrame = groups.rank(method="max", pct=True) * 100
    return ranks.where(groups.transform("count") >= MIN_PEERS)
