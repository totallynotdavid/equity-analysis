"""Groups SMV material-event rows into filings with a coarse topic.

The SMV changed its list of event kinds over the years: 2010 labels carry no
number and later ones do (`14. DISTRIBUCIÓN ...`), and some were reworded.
A topic is found by keywords in the unaccented label, so both spellings land
in the same topic. A filing that reports several kinds gets every matching
topic.
"""

import re

from typing import TYPE_CHECKING

import pandas as pd

from bvl import issuers, store


if TYPE_CHECKING:
    from pathlib import Path

# Topic -> keywords. The first topic whose keyword appears in a label wins.
TOPICS: dict[str, tuple[str, ...]] = {
    "results": ("INFORMACION FINANCIERA", "ESTADOS FINANCIEROS", "MEMORIA ANUAL"),
    "dividends": ("UTILIDADES", "DIVIDENDO", "FECHA DE REGISTRO"),
    "capital": (
        "CAPITAL",
        "FUSION",
        "ESCISION",
        "REORGANIZACION",
        "ADQUISICION",
        "ENAJENACION",
        "INVERSION O DESINVERSION",
    ),
    "debt": ("COLOCACION DE VALORES", "CLASIFICACION DE RIESGO", "REP.DE OBLIG"),
    "legal": ("SANCIONADOR", "DEMANDA", "JUDICIAL"),
    "governance": (
        "DIRECTORES",
        "GERENTES",
        "JUNTA",
        "ESTATUTO",
        "AUDITORIA",
        "COMITE",
        "GRUPO ECONOMICO",
    ),
    "news": ("PRENSA", "MEDIOS", "ANALISTAS"),
    "other": ("OTRAS CIRCUNSTANCIAS", "OTROS"),
}


def topic(kind: str) -> str:
    """The topic of one event-kind label, or `unclassified`."""
    label = re.sub(r"^\d+\.\s*", "", issuers.ascii_upper(kind))
    for name, keywords in TOPICS.items():
        if any(keyword in label for keyword in keywords):
            return name
    return "unclassified"


def load(db: Path) -> pd.DataFrame:
    """One row per filing: ruc, company, filed_at, agreed_on and its topics."""
    rows = store.read(
        db, "SELECT filing, ruc, company, kind, agreed_on, filed_at FROM events"
    )
    rows["topic"] = rows["kind"].map(topic)
    filings = rows.groupby("filing").agg(
        ruc=("ruc", "first"),
        company=("company", "first"),
        filed_at=("filed_at", "first"),
        agreed_on=("agreed_on", "min"),
        topics=("topic", lambda t: frozenset(t)),
    )
    filings["filed_at"] = pd.to_datetime(filings["filed_at"])
    filings["agreed_on"] = pd.to_datetime(filings["agreed_on"])
    return filings.reset_index()
