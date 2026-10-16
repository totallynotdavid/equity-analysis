"""SEC EDGAR company facts: what each company reported, and when it filed.

`parse_companyfacts` turns one `companyfacts` JSON document into the standard
facts frame. `EdgarSource` is the only caller of the network here, and it goes
through the paced, cached transports of `transport.py`.
"""

import os

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import httpx2
import pandas as pd

from index_core.sources.base import (
    FACT_COLUMNS,
    NotFoundError,
    Security,
    SourceError,
    empty_facts,
    normalise_ticker,
)
from index_core.sources.transport import CachingTransport, PacedTransport


if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date
    from pathlib import Path

DATA_URL = "https://data.sec.gov"
WWW_URL = "https://www.sec.gov"
USER_AGENT_VARIABLE = "SEC_USER_AGENT"
# SEC fair access allows 10 requests a second. Stay under it.
MIN_INTERVAL = 0.12

# Amendments restate the original, so they count as filings.
# Other forms do not carry the audited periods these features need.
FORMS = frozenset({"10-K", "10-K/A", "10-KT", "10-Q", "10-Q/A", "10-QT"})


@dataclass(frozen=True)
class Concept:
    """A quantity the features need and the XBRL tags that can carry it.

    Companies change tags over time (revenue has several), so the tags are in
    priority order and a period takes the first tag that reports it.

    A `flow` is a quantity over a period that adds up across periods. An
    `average` is a quantity over a period that does not, such as the weighted
    share count. An `instant` is a value on one date.

    A `payment` is kept as a positive amount paid. Filers differ on its sign.
    """

    kind: Literal["flow", "instant", "average"]
    unit: str
    tags: tuple[str, ...]
    taxonomy: str = "us-gaap"
    payment: bool = False


CONCEPTS = {
    "revenue": Concept(
        "flow",
        "USD",
        (
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "SalesRevenueNet",
            "RevenuesNetOfInterestExpense",
        ),
    ),
    "gross_profit": Concept("flow", "USD", ("GrossProfit",)),
    "operating_income": Concept("flow", "USD", ("OperatingIncomeLoss",)),
    "net_income": Concept("flow", "USD", ("NetIncomeLoss", "ProfitLoss")),
    "pretax_income": Concept(
        "flow",
        "USD",
        (
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        ),
    ),
    "income_tax": Concept("flow", "USD", ("IncomeTaxExpenseBenefit",)),
    "interest_expense": Concept(
        "flow",
        "USD",
        (
            "InterestExpense",
            "InterestExpenseNonoperating",
            "InterestExpenseDebt",
            "InterestAndDebtExpense",
        ),
        payment=True,
    ),
    "stock_compensation": Concept(
        "flow",
        "USD",
        ("ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"),
    ),
    "eps_diluted": Concept("flow", "USD/shares", ("EarningsPerShareDiluted",)),
    "diluted_shares": Concept(
        "average",
        "shares",
        ("WeightedAverageNumberOfDilutedSharesOutstanding",),
    ),
    "depreciation": Concept(
        "flow",
        "USD",
        (
            "DepreciationDepletionAndAmortization",
            "DepreciationAndAmortization",
            "DepreciationAmortizationAndAccretionNet",
        ),
    ),
    "operating_cash_flow": Concept(
        "flow", "USD", ("NetCashProvidedByUsedInOperatingActivities",)
    ),
    "capex": Concept(
        "flow",
        "USD",
        (
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsToAcquireProductiveAssets",
        ),
        payment=True,
    ),
    "total_assets": Concept("instant", "USD", ("Assets",)),
    "long_term_debt": Concept(
        "instant",
        "USD",
        (
            "LongTermDebtNoncurrent",
            "LongTermDebt",
            "LongTermDebtAndCapitalLeaseObligations",
        ),
    ),
    "current_debt": Concept("instant", "USD", ("DebtCurrent", "LongTermDebtCurrent")),
    "equity": Concept(
        "instant",
        "USD",
        (
            "StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        ),
    ),
    "cash": Concept(
        "instant",
        "USD",
        ("CashAndCashEquivalentsAtCarryingValue", "CashAndCashEquivalents"),
    ),
    "shares": Concept(
        "instant", "shares", ("EntityCommonStockSharesOutstanding",), taxonomy="dei"
    ),
}


def _is_balance(concept: str) -> bool:
    return CONCEPTS[concept].kind == "instant"


def filing_url(cik: int, accn: str) -> str:
    """The EDGAR page that lists the documents of one filing."""
    return (
        f"{WWW_URL}/Archives/edgar/data/{cik}/{accn.replace('-', '')}/{accn}-index.htm"
    )


def parse_companyfacts(payload: object, ticker: str = "?") -> pd.DataFrame:
    """The facts of one `companyfacts` document in the `FACT_COLUMNS` columns.

    Only the concepts in `CONCEPTS`, in their expected unit, from the forms in
    `FORMS`. `start` is NaT for a balance-sheet value. `priority` is the tag's
    position in its concept's list. A fact reported more than once keeps one row
    per filing, which is how a restatement shows up. A cover-page share count
    that a filing gives per class is summed. `accn`, `form`, `fy` and `fp` are
    the filing's own, as EDGAR sends them, and `cik` is the document's company.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("facts"), dict):
        raise SourceError(f"EDGAR sent no facts for {ticker}")
    taxonomies: dict[str, Any] = payload["facts"]

    rows: list[dict[str, Any]] = []
    try:
        for concept, spec in CONCEPTS.items():
            tags = taxonomies.get(spec.taxonomy, {})
            for priority, tag in enumerate(spec.tags):
                entries = tags.get(tag, {}).get("units", {}).get(spec.unit, [])
                rows.extend(
                    {
                        "concept": concept,
                        "start": entry.get("start") if spec.kind != "instant" else None,
                        "end": entry["end"],
                        "filed": entry["filed"],
                        "value": abs(entry["val"]) if spec.payment else entry["val"],
                        "priority": priority,
                        "accn": entry["accn"],
                        "form": entry["form"],
                        "fy": entry["fy"],
                        "fp": entry["fp"],
                        "cik": int(payload["cik"]),
                    }
                    for entry in entries
                    if entry.get("form") in FORMS
                )
    except (AttributeError, KeyError, TypeError) as error:
        raise SourceError(f"EDGAR sent malformed facts for {ticker}") from error
    if not rows:
        return empty_facts()

    frame = pd.DataFrame(rows)
    for column in ("start", "end", "filed"):
        frame[column] = pd.to_datetime(frame[column]).astype("datetime64[ns]")
    frame["value"] = frame["value"].astype(float)
    frame["fy"] = frame["fy"].astype(int)
    frame = frame.dropna(subset=["end", "filed", "value"])
    # A period without a start cannot be told apart from a quarter or a year.
    frame = frame[frame["start"].notna() | frame["concept"].map(_is_balance)]
    is_shares = frame["concept"] == "shares"
    per_filing = (
        frame[is_shares]
        .groupby(
            ["concept", "end", "filed", "priority", "accn", "form", "fy", "fp", "cik"],
            as_index=False,
        )["value"]
        .sum()
        .assign(start=pd.NaT)
    )
    frame = pd.concat([frame[~is_shares], per_filing])
    frame = frame.drop_duplicates(
        subset=["concept", "start", "end", "filed", "priority"], keep="last"
    )
    ordered: pd.DataFrame = frame[list(FACT_COLUMNS)].sort_values(
        ["concept", "end", "start", "filed", "priority"]
    )
    return ordered.reset_index(drop=True)


class EdgarSource:
    name = "edgar"

    def __init__(
        self,
        user_agent: str,
        transport: httpx2.BaseTransport | None = None,
        min_interval: float = MIN_INTERVAL,
        cache: Path | None = None,
        ciks: Mapping[str, tuple[int, ...]] | None = None,
    ) -> None:
        paced: httpx2.BaseTransport = PacedTransport(
            transport or httpx2.HTTPTransport(), min_interval
        )
        if cache is not None:
            paced = CachingTransport(paced, cache)
        self._client = httpx2.Client(
            headers={"User-Agent": user_agent}, timeout=30.0, transport=paced
        )
        self._known = {normalise_ticker(t): cik for t, cik in (ciks or {}).items()}
        self._listed: dict[str, int] | None = None

    @classmethod
    def from_env(
        cls,
        cache: Path | None = None,
        ciks: Mapping[str, tuple[int, ...]] | None = None,
    ) -> EdgarSource:
        user_agent = os.environ.get(USER_AGENT_VARIABLE)
        if not user_agent:
            raise SourceError(
                f"{USER_AGENT_VARIABLE} is not set; the SEC refuses a request "
                "without a User-Agent"
            )
        return cls(user_agent, cache=cache, ciks=ciks)

    def facts(self, ticker: str, end: date) -> pd.DataFrame:
        frames = [
            parse_companyfacts(
                self._get_json(
                    f"{DATA_URL}/api/xbrl/companyfacts/CIK{cik:010d}.json",
                    per_company=True,
                ),
                ticker,
            )
            for cik in self._ciks(ticker)
        ]
        frame = frames[0] if len(frames) == 1 else pd.concat(frames)
        frame = frame.sort_values(["concept", "end", "start", "filed", "priority"])
        frame = frame[frame["filed"] <= pd.Timestamp(end)]
        return frame.reset_index(drop=True)

    def security(self, ticker: str) -> Security:
        """Who the ticker is now: the first of its registrants."""
        cik = self._ciks(ticker)[0]
        document = self._get_json(
            f"{DATA_URL}/submissions/CIK{cik:010d}.json", per_company=True
        )
        if not isinstance(document, dict):
            raise SourceError(f"EDGAR sent no submissions for {ticker}")
        try:
            return Security(
                cik=cik,
                name=str(document["name"]),
                sic=str(document.get("sic") or ""),
                sic_description=str(document.get("sicDescription") or ""),
            )
        except KeyError as error:
            raise SourceError(
                f"EDGAR sent malformed submissions for {ticker}"
            ) from error

    def _ciks(self, ticker: str) -> tuple[int, ...]:
        """The registrants of the company, current first: those given, else the
        one in the SEC's ticker list, which is fetched on the first ticker that
        needs it.
        """
        key = normalise_ticker(ticker)
        if key in self._known:
            return self._known[key]
        if self._listed is None:
            listing = self._get_json(f"{WWW_URL}/files/company_tickers.json")
            if not isinstance(listing, dict):
                raise SourceError("EDGAR sent no ticker list")
            try:
                self._listed = {
                    str(entry["ticker"]).upper(): int(entry["cik_str"])
                    for entry in listing.values()
                }
            except (KeyError, TypeError, ValueError) as error:
                raise SourceError("EDGAR sent a malformed ticker list") from error
        cik = self._listed.get(key)
        if cik is None:
            raise NotFoundError(f"EDGAR has no company for ticker {ticker}")
        return (cik,)

    def _get_json(self, url: str, *, per_company: bool = False) -> object:
        try:
            response = self._client.get(url)
        except httpx2.HTTPError as error:
            raise SourceError(f"could not reach EDGAR at {url}: {error!r}") from error
        if response.status_code == 404 and per_company:
            raise NotFoundError(f"EDGAR has no facts at {url}")
        if response.status_code != 200:
            raise SourceError(
                f"EDGAR returned HTTP {response.status_code} for {url}: "
                f"{response.text[:200]}"
            )
        try:
            return response.json()
        except ValueError as error:
            raise SourceError(f"EDGAR sent invalid JSON for {url}") from error
