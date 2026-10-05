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
    table["score"] = SCORE_LEVELS - (table["rank"] - 1) * SCORE_LEVELS // count
    return table
