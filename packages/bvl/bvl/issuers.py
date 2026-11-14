"""Links BVL tickers to the SMV issuers that file events, by company name.

Quotes name a ticker's company (`Banco De Credito Del Peru`) but carry no RUC;
events carry the RUC and the registered name (`BANCO DE CREDITO DEL PERU`).
Names are compared after `normalise`. The quotes table also spells some
names with a broken `ñ` (`Compaqia`), which `normalise` cannot repair, so
`OVERRIDES` names those and any pair the rule gets wrong.
"""

import re
import unicodedata

from typing import TYPE_CHECKING

import pandas as pd


if TYPE_CHECKING:
    from collections.abc import Mapping

# Legal forms and status words that differ between the two sources.
_NOISE = frozenset(
    {
        "S",
        "A",
        "SA",
        "SAA",
        "SAC",
        "LTD",
        "LIMITED",
        "INC",
        "EN",
        "LIQUIDACION",
        "Y",
        "DE",
        "DEL",
        "LA",
        "LOS",
        "EL",
    }
)
# Foreign issuers file without a Peruvian RUC, under this placeholder.
NO_RUC = "0"
# ticker -> RUC, for names the rule cannot match: renamed issuers, holdings
# that kept an old ticker, and broken spellings.
OVERRIDES: Mapping[str, str] = {
    "AENZAC1": "20332600592",
    "ATACOAC1": "20100123500",
    "ATACOBC1": "20100123500",
    "BVLAC1": "20100055661",
    "CASAGRC1": "20131823020",
    "CEMLIMC1": "20100137390",
    "CEMLIMI1": "20100137390",
    "CONCESI1": "20518410858",
    "CONTINC1": "20100130204",
    "EDEGELC1": "20330791412",
    "EDELNOC1": "20269985900",
    "ELPIUBC1": "20270508163",
    "ENDISPC1": "20269985900",
    "ENERSUC1": "20333363900",
    "ENGEPEC1": "20330791412",
    "FALABEC1": "20260172035",
    "FERREYC1": "20100027292",
    "GLORIAI1": "20100190797",
    "GRAMONC1": "20332600592",
    "LAREDOC1": "20132377783",
    "MILPOC1": "20100110513",
    "MILPOI1": "20100110513",
    "RELAPAC1": "20259829594",
    "SIDERC1": "20402885549",
    "SPCCPI1": "20100147514",
    "TELEFBC1": "20100017491",
    "UNACEMC1": "20100137390",
}


def ascii_upper(text: str) -> str:
    """Upper case with accents dropped: `Compañía` -> `COMPANIA`."""
    return (
        unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().upper()
    )


def normalise(name: str) -> str:
    """Upper case, no accents, punctuation, legal forms or `(Antes ...)` notes."""
    plain = re.sub(r"\(.*?(\)|$)", " ", ascii_upper(name))
    words = re.sub(r"[^A-Z0-9 ]", " ", plain).split()
    return " ".join(word for word in words if word not in _NOISE)


def link(tickers: pd.DataFrame, filers: pd.DataFrame) -> pd.DataFrame:
    """Match `tickers` (ticker, name) to `filers` (ruc, company) by name.

    A ticker may appear once per name it has carried, newest first; its first
    name that matches wins. Returns one row per matched ticker: ticker, ruc,
    how (`name` or `override`). A normalised name shared by two RUCs matches
    neither, and so does a filer without a RUC.
    """
    filers = filers[filers["ruc"] != NO_RUC]
    keys = filers.assign(key=filers["company"].map(normalise))
    keys = keys.drop_duplicates(["key", "ruc"])
    rucs_per_key = keys.groupby("key")["ruc"].transform("size")
    unique = keys[rucs_per_key == 1].set_index("key")["ruc"]
    matched = tickers.assign(ruc=tickers["name"].map(normalise).map(unique), how="name")
    overrides = pd.DataFrame(
        {"ticker": list(OVERRIDES), "ruc": list(OVERRIDES.values()), "how": "override"}
    )
    matched = pd.concat([overrides, matched.dropna(subset=["ruc"])])
    matched = matched.drop_duplicates("ticker")
    return matched[["ticker", "ruc", "how"]].reset_index(drop=True)
