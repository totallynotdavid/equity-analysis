from typing import TYPE_CHECKING

import numpy as np


if TYPE_CHECKING:
    import pandas as pd


SCORE_LEVELS = 10


def decile_scores(probabilities: pd.Series) -> pd.DataFrame:
    """Rank 1 is the highest probability. Score 10 is the top tenth.

    `probabilities` is indexed by ticker. Ties break by ticker, so the ranks are
    unique and repeatable. The result has `ticker`, `prob`, `rank` and `score`
    columns, ordered by rank.
    """
    table = (
        probabilities.rename("prob")
        .rename_axis("ticker")
        .reset_index()
        .sort_values(["prob", "ticker"], ascending=[False, True], ignore_index=True)
    )
    count = len(table)
    table["rank"] = np.arange(1, count + 1)
    table["score"] = _score(table["rank"], count)
    return table


def scores_by_date(probabilities: pd.Series) -> pd.Series:
    """The score of every row, ranked among the stocks of its own date.

    `probabilities` is indexed by (`date`, `ticker`). The scores equal
    `decile_scores` applied to each date alone.
    """
    table = (
        probabilities.rename("prob")
        .reset_index()
        .sort_values(
            ["date", "prob", "ticker"], ascending=[True, False, True], ignore_index=True
        )
    )
    by_date = table.groupby("date")
    rank = by_date.cumcount() + 1
    table["score"] = _score(rank, by_date["prob"].transform("size"))
    return table.set_index(["date", "ticker"])["score"]


def _score(rank: pd.Series, count: int | pd.Series) -> pd.Series:
    return SCORE_LEVELS - (rank - 1) * SCORE_LEVELS // count
