"""The published shape of a scoring run. `scores.json` and the API share it."""

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


class ScoresReport(BaseModel):
    status: Literal["experimental, not validated"] = "experimental, not validated"
    as_of: date
    universe: str
    price_source: str
    facts_source: str | None
    horizon_days: int
    model: ModelInfo
    rows: list[ScoreRow]
