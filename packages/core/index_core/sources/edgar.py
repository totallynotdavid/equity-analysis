"""SEC EDGAR company facts: what each company reported, and when it filed.

`parse_companyfacts` turns one `companyfacts` JSON document into the standard
facts frame. Network I/O lives in `EdgarSource` and nowhere else.
"""

import os
import time

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import httpx2
import pandas as pd

from index_core.sources.base import (
    FACT_COLUMNS,
    NotFoundError,
    SourceError,
    empty_facts,
)


if TYPE_CHECKING:
    from datetime import date

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

    A `payment` is kept as a positive amount paid. Filers differ on its sign.
    """

    kind: Literal["flow", "instant"]
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
        ),
    ),
    "gross_profit": Concept("flow", "USD", ("GrossProfit",)),
    "operating_income": Concept("flow", "USD", ("OperatingIncomeLoss",)),
    "net_income": Concept("flow", "USD", ("NetIncomeLoss", "ProfitLoss")),
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
        "instant", "USD", ("LongTermDebtNoncurrent", "LongTermDebt")
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


def parse_companyfacts(payload: object, ticker: str = "?") -> pd.DataFrame:
    """The facts of one `companyfacts` document in the `FACT_COLUMNS` columns.

    Only the concepts in `CONCEPTS`, in their expected unit, from the forms in
    `FORMS`. `start` is NaT for a balance-sheet value. `priority` is the tag's
    position in its concept's list. A fact reported more than once keeps one row
    per filing, which is how a restatement shows up. A cover-page share count
    that a filing gives per class is summed.
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
                        "start": entry.get("start") if spec.kind == "flow" else None,
                        "end": entry["end"],
                        "filed": entry["filed"],
                        "value": abs(entry["val"]) if spec.payment else entry["val"],
                        "priority": priority,
                        "accn": entry.get("accn", ""),
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
    frame = frame.dropna(subset=["end", "filed", "value"])
    # A period without a start cannot be told apart from a quarter or a year.
    frame = frame[frame["start"].notna() | frame["concept"].map(_is_balance)]
    is_shares = frame["concept"] == "shares"
    per_filing = (
        frame[is_shares]
        .groupby(["concept", "end", "filed", "priority", "accn"], as_index=False)[
            "value"
        ]
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
    ) -> None:
        self._client = httpx2.Client(
            headers={"User-Agent": user_agent},
            timeout=30.0,
            transport=transport,
        )
        self._min_interval = min_interval
        self._last_request = 0.0
        self._ciks: dict[str, int] | None = None

    @classmethod
    def from_env(cls) -> EdgarSource:
        user_agent = os.environ.get(USER_AGENT_VARIABLE)
        if not user_agent:
            raise SourceError(
                f"{USER_AGENT_VARIABLE} is not set; SEC requires a User-Agent "
                'with a contact, such as "Jane Doe jane@example.com"'
            )
        return cls(user_agent)

    def facts(self, ticker: str, end: date) -> pd.DataFrame:
        cik = self._cik(ticker)
        frame = parse_companyfacts(
            self._get_json(
                f"{DATA_URL}/api/xbrl/companyfacts/CIK{cik:010d}.json",
                per_company=True,
            ),
            ticker,
        )
        return frame[frame["filed"] <= pd.Timestamp(end)].reset_index(drop=True)

    def _cik(self, ticker: str) -> int:
        if self._ciks is None:
            listing = self._get_json(f"{WWW_URL}/files/company_tickers.json")
            if not isinstance(listing, dict):
                raise SourceError("EDGAR sent no ticker list")
            try:
                self._ciks = {
                    str(entry["ticker"]).upper(): int(entry["cik_str"])
                    for entry in listing.values()
                }
            except (KeyError, TypeError, ValueError) as error:
                raise SourceError("EDGAR sent a malformed ticker list") from error
        cik = self._ciks.get(ticker.upper().replace(".", "-"))
        if cik is None:
            raise NotFoundError(f"EDGAR has no company for ticker {ticker}")
        return cik

    def _get_json(self, url: str, *, per_company: bool = False) -> object:
        wait = self._last_request + self._min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            response = self._client.get(url)
        except httpx2.HTTPError as error:
            raise SourceError(f"could not reach EDGAR at {url}: {error!r}") from error
        finally:
            self._last_request = time.monotonic()
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
