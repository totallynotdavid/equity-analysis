import numpy as np
import pandas as pd
import pytest

from index_core.baselines import FAMILIES, NEUTRAL, composite_score, fit_ridge


COLUMNS = sorted({name.lstrip("-") for names in FAMILIES.values() for name in names})


def _ranks(stocks: int = 40, seed: int = 0) -> pd.DataFrame:
    """Percentile ranks of every feature the families read, for one date."""
    rng = np.random.default_rng(seed)
    frame = pd.DataFrame(
        rng.random((stocks, len(COLUMNS))),
        columns=COLUMNS,
        index=pd.Index([f"S{i:02d}" for i in range(stocks)], name="ticker"),
    )
    return frame.rank(pct=True)


def test_a_stock_ranked_best_on_value_quality_and_momentum_scores_highest() -> None:
    ranks = _ranks()
    best = {name: 0.0 if name == "accruals" else 1.0 for name in COLUMNS}
    ranks.loc["S00"] = pd.Series(best)

    score = composite_score(ranks)

    assert score.idxmax() == "S00"
    assert score["S00"] == pytest.approx(1.0)


def test_high_accruals_lower_the_quality_family() -> None:
    ranks = _ranks()
    low = ranks.copy()
    low.loc["S01", "accruals"] = 0.0
    high = ranks.copy()
    high.loc["S01", "accruals"] = 1.0

    assert composite_score(low)["S01"] > composite_score(high)["S01"]


def test_a_stock_without_fundamentals_gets_the_neutral_rank_for_those_families() -> (
    None
):
    ranks = _ranks()
    fundamentals = [name for name in COLUMNS if name != "mom_12_1"]
    ranks.loc["S02", fundamentals] = np.nan
    ranks.loc["S02", "mom_12_1"] = 0.9

    score = composite_score(ranks)

    assert score["S02"] == pytest.approx((NEUTRAL + NEUTRAL + 0.9) / 3)


def _training(rows: int = 4000, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    train = pd.DataFrame(rng.random((rows, len(COLUMNS))), columns=COLUMNS)
    chance = 0.5 + 0.8 * (train["roa"] - 0.5)
    train["label"] = (rng.random(rows) < chance).astype(float)
    return train


def test_ridge_puts_its_weight_on_the_feature_that_carries_the_label() -> None:
    train = _training()

    model = fit_ridge(train, COLUMNS)

    weights = dict(zip(COLUMNS, model.coefficients, strict=True))
    assert weights["roa"] > 0
    assert weights["roa"] > 5 * max(
        abs(value) for name, value in weights.items() if name != "roa"
    )


def test_ridge_scores_rank_a_fresh_sample_in_the_direction_of_the_signal() -> None:
    model = fit_ridge(_training(), COLUMNS)
    fresh = _training(rows=1000, seed=2)

    scores = pd.Series(model.predict(fresh))

    assert scores.corr(fresh["roa"], method="spearman") > 0.8


def test_ridge_treats_a_missing_feature_as_the_neutral_rank() -> None:
    model = fit_ridge(_training(), COLUMNS)
    row = pd.DataFrame([dict.fromkeys(COLUMNS, NEUTRAL)])
    gap = row.copy()
    gap["roa"] = np.nan

    assert model.predict(gap) == pytest.approx(model.predict(row))
