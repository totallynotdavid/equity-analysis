"""The published shapes of a scoring run and of a ticker. `scores.json`, the
ticker files and the API share them."""

from datetime import date
from typing import Literal

from pydantic import BaseModel


class ScoreRow(BaseModel):
    ticker: str
    rank: int
    score: int
    prob: float


class ModelInfo(BaseModel):
    train_rows: int
    holdout_rows: int
    holdout_auc: float | None


class LeftOut(BaseModel):
    ticker: str
    reason: Literal["no prices", "no filings"]


class ScoresReport(BaseModel):
    status: Literal["experimental, not validated"] = "experimental, not validated"
    as_of: date
    universe: str
    price_source: str
    facts_source: str | None
    horizon_days: int
    model: ModelInfo
    rows: list[ScoreRow]
    left_out: list[LeftOut] = []


class Filing(BaseModel):
    """The filing that reported a figure: its accession number, its form, the
    fiscal year and period it describes, the day it reached the SEC and the
    EDGAR page that lists its documents."""

    accn: str
    form: str
    fy: int
    fp: str
    filed: date
    url: str


class Part(BaseModel):
    """One reported figure a point is made of. `metric` is the quantity it
    reports and `tag` the XBRL tag it was reported under. `start` is empty for a
    balance-sheet value. `sign` is 1 where the figure is added to the point and
    -1 where it is subtracted."""

    metric: str
    sign: Literal[1, -1]
    tag: str
    start: date | None
    end: date
    value: float
    filing: Filing


class Point(BaseModel):
    """The value of a metric for the twelve months to `end`, or on `end` for a
    balance-sheet quantity. `parts` are the filed figures it is made of and
    `formula` says how: `None` where the value is the sum of the signed parts."""

    end: date
    value: float
    formula: str | None = None
    parts: list[Part]


class Series(BaseModel):
    """One metric over the latest periods, oldest first, with where its latest
    value stands in the universe and in the sector, in percent of the group at
    or below it."""

    metric: str
    group: str
    label: str
    unit: str
    pct_universe: float | None
    pct_sector: float | None
    points: list[Point]


class TickerReport(BaseModel):
    """The fundamentals of a ticker as of a date. `facts_source` says where the
    filings came from: `synthetic` ones have no real filing behind them."""

    ticker: str
    universe: str
    as_of: date
    facts_source: str
    name: str
    cik: int
    sic: str
    sic_description: str
    sector: str | None
    series: list[Series]
