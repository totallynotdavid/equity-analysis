import numpy as np
import pandas as pd
import pytest

from bvl import liquidity, panel


def test_time_effects_recover_year_effects_on_an_unbalanced_panel() -> None:
    rng = np.random.default_rng(7)
    year_effect = {2000: 0.0, 2001: 1.0, 2002: -0.5, 2003: 2.0}
    rows = []
    for unit in range(40):
        # Units enter and leave at different years, and each has its own level,
        # as a dollar stock's ratio sits a constant factor from a sol stock's.
        level = rng.normal(0, 3)
        first = int(rng.integers(2000, 2003))
        for year in range(first, int(rng.integers(first + 1, 2004)) + 1):
            for _ in range(3):
                value = level + year_effect[year] + rng.normal(0, 0.01)
                rows.append((unit, year, value))
    frame = pd.DataFrame(rows, columns=["unit", "year", "value"])

    effects = liquidity.time_effects(frame["value"], frame["unit"], frame["year"])

    expected = pd.Series(year_effect)
    expected -= expected.mean()
    assert effects.to_numpy() == pytest.approx(expected.to_numpy(), abs=0.02)


@pytest.mark.parametrize(
    ("ticker", "name", "local"),
    [
        ("ALICORC1", "Alicorp S.A.A.", True),
        ("BACKUSI1", "Union De Cervecerias Peruanas Backus Y Johnston", True),
        ("AMD", "Advanced Micro Devices, Inc.", False),
        ("BAP", "Credicorp Ltd.", False),
        ("FICORPS2", "Coril Renta Prime Soles 2 - Fondo De Inversion", False),
        ("COWZ1", "Pacerus Cashcows100uts Etfacc", False),
        ("SPXS1", "Invesco S&P500 Ucits Etf", False),
    ],
)
def test_local_shares_exclude_foreign_listings_funds_and_etfs(
    ticker: str, name: str, *, local: bool
) -> None:
    assert panel.is_local_share(ticker, name) is local
