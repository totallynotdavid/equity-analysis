"""SQLite storage for prices and scores."""

import os
import sqlite3

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Self

import pandas as pd

from index_core.report import ModelInfo, ScoreRow, ScoresReport
from index_core.sources.base import PRICE_COLUMNS


if TYPE_CHECKING:
    from types import TracebackType

DB_VARIABLE = "INDEX_DB"

# Raise it when `_SCHEMA` changes. A database is rebuilt, never migrated.
SCHEMA_VERSION = 1

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prices (
    ticker TEXT NOT NULL,
    date TEXT NOT NULL,
    {", ".join(f"{column} REAL NOT NULL" for column in PRICE_COLUMNS)},
    PRIMARY KEY (ticker, date)
);
CREATE TABLE IF NOT EXISTS facts (
    ticker TEXT NOT NULL,
    concept TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    filed TEXT NOT NULL,
    value REAL NOT NULL,
    priority INTEGER NOT NULL,
    PRIMARY KEY (ticker, concept, start, end, filed, priority)
);
CREATE TABLE IF NOT EXISTS runs (
    as_of TEXT NOT NULL,
    universe TEXT NOT NULL,
    source TEXT NOT NULL,
    horizon_days INTEGER NOT NULL,
    train_rows INTEGER NOT NULL,
    holdout_rows INTEGER NOT NULL,
    holdout_auc REAL,
    PRIMARY KEY (as_of, universe)
);
CREATE TABLE IF NOT EXISTS scores (
    as_of TEXT NOT NULL,
    universe TEXT NOT NULL,
    ticker TEXT NOT NULL,
    rank INTEGER NOT NULL,
    score INTEGER NOT NULL,
    prob REAL NOT NULL,
    PRIMARY KEY (as_of, universe, ticker),
    FOREIGN KEY (as_of, universe) REFERENCES runs (as_of, universe)
);
"""


class StoreError(Exception):
    pass


def default_db_path() -> Path:
    """Use `$INDEX_DB`. Otherwise use `data/index.sqlite`."""
    return Path(os.environ.get(DB_VARIABLE, "data/index.sqlite"))


def _require_current_layout(connection: sqlite3.Connection, path: Path) -> None:
    """Close `connection` and raise `StoreError` if it holds tables made by a
    layout other than `_SCHEMA`."""
    has_tables = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name = 'prices'"
    ).fetchone()
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if has_tables and version != SCHEMA_VERSION:
        connection.close()
        raise StoreError(f"{path} has an older layout; delete it and run again")


class Store:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    @classmethod
    def open(cls, path: Path, *, read_only: bool = False) -> Self:
        if read_only:
            connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
            _require_current_layout(connection, path)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(path)
            connection.execute("PRAGMA journal_mode=WAL")
            _require_current_layout(connection, path)
            connection.executescript(_SCHEMA)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        return cls(connection)

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def require_sources(self, prices: str, filings: str) -> None:
        """Raise `StoreError` if the stored prices or facts come from other sources.

        One database holds prices from one source and facts from one. Mixing,
        say, synthetic and real data would make every score downstream
        meaningless. Call it before fetching: the writes check too, but only
        after the first request.
        """
        self._require("price_source", "prices", prices)
        self._require("facts_source", "facts", filings)

    def _require(self, key: str, kind: str, source: str) -> None:
        stored = self._db.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        if stored is not None and stored[0] != source:
            raise StoreError(
                f"this database holds {stored[0]} {kind}; use a new database "
                f"for {source}"
            )

    def upsert_prices(self, source: str, ticker: str, bars: pd.DataFrame) -> None:
        """Store bars, replacing any stored for the same ticker and date."""
        with self._db:
            self._require("price_source", "prices", source)
            self._db.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES ('price_source', ?)",
                (source,),
            )
            columns = ", ".join(PRICE_COLUMNS)
            marks = ", ".join("?" * (2 + len(PRICE_COLUMNS)))
            self._db.executemany(
                f"INSERT OR REPLACE INTO prices (ticker, date, {columns}) "
                f"VALUES ({marks})",
                (
                    (ticker, day.date().isoformat(), *row)
                    for day, row in zip(
                        bars.index,
                        bars[list(PRICE_COLUMNS)].itertuples(index=False),
                        strict=True,
                    )
                ),
            )

    def price_source(self) -> str | None:
        """The source of the stored prices, or None before any were stored."""
        stored = self._db.execute(
            "SELECT value FROM meta WHERE key = 'price_source'"
        ).fetchone()
        return None if stored is None else str(stored[0])

    def priced_tickers(self) -> set[str]:
        """Every ticker with at least one stored bar."""
        return {
            str(row[0])
            for row in self._db.execute("SELECT DISTINCT ticker FROM prices")
        }

    def read_prices(self, tickers: list[str]) -> pd.DataFrame:
        """Long frame with `ticker`, a `date` column and the price columns."""
        marks = ", ".join("?" * len(tickers))
        columns = ", ".join(PRICE_COLUMNS)
        frame = pd.read_sql_query(
            f"SELECT ticker, date, {columns} FROM prices "
            f"WHERE ticker IN ({marks}) ORDER BY ticker, date",
            self._db,
            params=tickers,
        )
        frame["date"] = pd.to_datetime(frame["date"])
        return frame

    def replace_facts(self, source: str, ticker: str, facts: pd.DataFrame) -> None:
        """Store all facts of a ticker, dropping any stored before.

        A company's facts are fetched whole each time.
        """
        with self._db:
            self._require("facts_source", "facts", source)
            if not facts.empty:
                self._db.execute(
                    "INSERT OR IGNORE INTO meta (key, value) "
                    "VALUES ('facts_source', ?)",
                    (source,),
                )
            self._db.execute("DELETE FROM facts WHERE ticker = ?", (ticker,))
            days = {
                column: facts[column].dt.strftime("%Y-%m-%d")
                for column in ("start", "end", "filed")
            }
            self._db.executemany(
                "INSERT INTO facts VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    (ticker, concept, start, end, filed, value, priority)
                    for concept, start, end, filed, value, priority in zip(
                        facts["concept"],
                        days["start"].fillna(""),
                        days["end"],
                        days["filed"],
                        facts["value"],
                        facts["priority"],
                        strict=True,
                    )
                ),
            )

    def facts_source(self) -> str | None:
        """The source of the stored facts, or None before any were stored."""
        stored = self._db.execute(
            "SELECT value FROM meta WHERE key = 'facts_source'"
        ).fetchone()
        return None if stored is None else str(stored[0])

    def read_facts(self, tickers: list[str]) -> pd.DataFrame:
        """Long frame with `ticker` and the `FACT_COLUMNS`."""
        marks = ", ".join("?" * len(tickers))
        frame = pd.read_sql_query(
            "SELECT ticker, concept, start, end, filed, value, priority FROM facts "
            f"WHERE ticker IN ({marks}) ORDER BY ticker, concept, end, start, filed",
            self._db,
            params=tickers,
        )
        frame["start"] = frame["start"].replace("", None)
        for column in ("start", "end", "filed"):
            frame[column] = pd.to_datetime(frame[column]).astype("datetime64[ns]")
        return frame

    def save_report(self, report: ScoresReport) -> None:
        """Store a run, replacing any earlier run of the same universe and date."""
        as_of = report.as_of.isoformat()
        key = (as_of, report.universe)
        with self._db:
            self._db.execute("DELETE FROM scores WHERE as_of = ? AND universe = ?", key)
            self._db.execute("DELETE FROM runs WHERE as_of = ? AND universe = ?", key)
            self._db.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    *key,
                    report.price_source,
                    report.horizon_days,
                    report.model.train_rows,
                    report.model.holdout_rows,
                    report.model.holdout_auc,
                ),
            )
            self._db.executemany(
                "INSERT INTO scores VALUES (?, ?, ?, ?, ?, ?)",
                (
                    (*key, row.ticker, row.rank, row.score, row.prob)
                    for row in report.rows
                ),
            )

    def latest_report(self, universe: str | None = None) -> ScoresReport | None:
        """The run with the latest as-of date, the last saved on a tie.

        Without `universe` it is the latest across all universes.
        """
        run = self._db.execute(
            "SELECT as_of, universe, horizon_days, train_rows, holdout_rows, "
            "holdout_auc FROM runs "
            "WHERE ?1 IS NULL OR universe = ?1 "
            "ORDER BY as_of DESC, rowid DESC LIMIT 1",
            (universe,),
        ).fetchone()
        if run is None:
            return None
        rows = self._db.execute(
            "SELECT ticker, rank, score, prob FROM scores "
            "WHERE as_of = ? AND universe = ? ORDER BY rank",
            (run[0], run[1]),
        ).fetchall()
        return ScoresReport(
            as_of=date.fromisoformat(run[0]),
            universe=run[1],
            price_source=self.price_source() or "unknown",
            facts_source=self.facts_source(),
            horizon_days=run[2],
            model=ModelInfo(train_rows=run[3], holdout_rows=run[4], holdout_auc=run[5]),
            rows=[
                ScoreRow(ticker=t, rank=rank, score=score, prob=prob)
                for t, rank, score, prob in rows
            ],
        )
