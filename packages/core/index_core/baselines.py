"""Two baselines that the LightGBM model has to beat.

Both read the percentile-ranked features of `features/normalize.py`. A missing
feature counts as the median rank, 0.5, so every model scores the same rows.

`composite_score` fits nothing: its families and signs are fixed here, from the
published anomalies, and not chosen on any data of this project. `Ridge` fits a
linear model on the same rows as the booster, with one fixed penalty.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd


if TYPE_CHECKING:
    from collections.abc import Mapping

NEUTRAL = 0.5

# Each family is a mean of ranks.
# A feature that is better when low is written as `-name`.
FAMILIES: Mapping[str, tuple[str, ...]] = {
    "value": ("sales_to_ev", "ebitda_to_ev", "earnings_yield", "fcf_yield"),
    "quality": ("roa", "gross_margin", "op_margin", "net_margin", "-accruals"),
    "momentum": ("mom_12_1",),
}

# The penalty is this many times the number of training rows, on standardised
# features: a coefficient shrinks by about half. It is fixed, not tuned.
RIDGE_PENALTY = 1.0


def composite_score(features: pd.DataFrame) -> pd.Series:
    """The equal-weight mean of the value, quality and momentum family ranks."""
    families = pd.DataFrame(
        {
            family: pd.concat([_signed(features, name) for name in names], axis=1)
            .mean(axis=1)
            .fillna(NEUTRAL)
            for family, names in FAMILIES.items()
        }
    )
    return families.mean(axis=1).rename("composite")


def _signed(features: pd.DataFrame, name: str) -> pd.Series:
    """A feature the run has no filings for is missing on every row."""
    column = name.removeprefix("-")
    ranks = (
        features[column] if column in features else pd.Series(np.nan, features.index)
    )
    return 1 - ranks if name.startswith("-") else ranks


@dataclass(frozen=True)
class Ridge:
    columns: list[str]
    mean: np.ndarray
    scale: np.ndarray
    coefficients: np.ndarray

    def predict(self, features: pd.DataFrame) -> np.ndarray:
        design = (self._filled(features) - self.mean) / self.scale
        return np.asarray(design @ self.coefficients)

    def _filled(self, features: pd.DataFrame) -> np.ndarray:
        return features[self.columns].fillna(NEUTRAL).to_numpy(dtype=float)


def fit_ridge(train: pd.DataFrame, columns: list[str]) -> Ridge:
    """Ridge regression of the 0/1 `label` on the ranked `columns`."""
    raw = train[columns].fillna(NEUTRAL).to_numpy(dtype=float)
    mean = raw.mean(axis=0)
    scale = raw.std(axis=0)
    scale[scale == 0] = 1.0
    design = (raw - mean) / scale
    target = train["label"].to_numpy(dtype=float)
    target = target - target.mean()
    penalty = RIDGE_PENALTY * len(train)
    gram = design.T @ design + penalty * np.eye(len(columns))
    return Ridge(columns, mean, scale, np.linalg.solve(gram, design.T @ target))
