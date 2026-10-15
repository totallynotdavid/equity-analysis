"""SQLite storage for prices and scores."""

import os
import sqlite3

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Self

import pandas as pd

from index_core.report import LeftOut, ModelInfo, ScoreRow, ScoresReport
from index_core.sources.base import FACTOR_COLUMNS, PRICE_COLUMNS


if TYPE_CHECKING:
    from types import TracebackType

DB_VARIABLE = "INDEX_DB"

# Raise it when `_SCHEMA` changes. A database is rebuilt, never migrated.
SCHEMA_VERSION = 3

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
CREATE TABLE IF NOT EXISTS factor_returns (
    date TEXT PRIMARY KEY,
    {", ".join(f"{column} REAL NOT NULL" for column in FACTOR_COLUMNS)}
);
CREATE TABLE IF NOT EXISTS trials (
    spec_hash TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    as_of TEXT NOT NULL,
    months INTEGER NOT NULL,
    sharpe REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    as_of TEXT NOT NULL,
    universe TEXT NOT NULL,
    price_source TEXT NOT NULL,
    facts_source TEXT,
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
CREATE TABLE IF NOT EXISTS left_out (
    as_of TEXT NOT NULL,
    universe TEXT NOT NULL,
    ticker TEXT NOT NULL,
    reason TEXT NOT NULL,
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

    def replace_factors(self, source: str, factors: pd.DataFrame) -> None:
        """Store the factor returns, dropping any stored before.

        A source serves its whole history each time.
        """
        with self._db:
            self._require("factors_source", "factors", source)
            self._db.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES ('factors_source', ?)",
                (source,),
            )
            self._db.execute("DELETE FROM factor_returns")
            marks = ", ".join("?" * (1 + len(FACTOR_COLUMNS)))
            self._db.executemany(
                f"INSERT INTO factor_returns VALUES ({marks})",
                (
                    (day.date().isoformat(), *row)
                    for day, row in zip(
                        factors.index,
                        factors[list(FACTOR_COLUMNS)].itertuples(index=False),
                        strict=True,
                    )
                ),
            )

    def factors_source(self) -> str | None:
        """The source of the stored factors, or None before any were stored."""
        stored = self._db.execute(
            "SELECT value FROM meta WHERE key = 'factors_source'"
        ).fetchone()
        return None if stored is None else str(stored[0])

    def read_factors(self) -> pd.DataFrame:
        """The `FACTOR_COLUMNS` by `date`. Empty before any were stored."""
        frame = pd.read_sql_query(
            f"SELECT date, {', '.join(FACTOR_COLUMNS)} FROM factor_returns "
            "ORDER BY date",
            self._db,
        )
        frame.index = pd.DatetimeIndex(pd.to_datetime(frame.pop("date")), name="date")
        return frame

    def record_trial(
        self, spec_hash: str, description: str, as_of: date, months: int, sharpe: float
    ) -> None:
        """Add a backtest specification to the ledger, or replace its result.

        A specification is counted once however often it is run.
        """
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO trials VALUES (?, ?, ?, ?, ?)",
                (spec_hash, description, as_of.isoformat(), months, sharpe),
            )

    def trial_sharpes(self) -> list[float]:
        """The monthly Sharpe ratio of every specification in the ledger."""
        return [
            float(row[0])
            for row in self._db.execute("SELECT sharpe FROM trials ORDER BY spec_hash")
        ]

    def save_report(self, report: ScoresReport) -> None:
        """Store a run, replacing any earlier run of the same universe and date.

        The run keeps its own sources, so a database that holds only a report,
        as `eq import` makes, still says where its scores came from.
        """
        as_of = report.as_of.isoformat()
        key = (as_of, report.universe)
        with self._db:
            for table in ("scores", "left_out"):
                self._db.execute(
                    f"DELETE FROM {table} WHERE as_of = ? AND universe = ?", key
                )
            self._db.execute("DELETE FROM runs WHERE as_of = ? AND universe = ?", key)
            self._db.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    *key,
                    report.price_source,
                    report.facts_source,
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
            self._db.executemany(
                "INSERT INTO left_out VALUES (?, ?, ?, ?)",
                ((*key, item.ticker, item.reason) for item in report.left_out),
            )

    def latest_report(self, universe: str | None = None) -> ScoresReport | None:
        """The run with the latest as-of date, the last saved on a tie.

        Without `universe` it is the latest across all universes.
        """
        run = self._db.execute(
            "SELECT as_of, universe, price_source, facts_source, horizon_days, "
            "train_rows, holdout_rows, holdout_auc FROM runs "
            "WHERE ?1 IS NULL OR universe = ?1 "
            "ORDER BY as_of DESC, rowid DESC LIMIT 1",
            (universe,),
        ).fetchone()
        if run is None:
            return None
        rows = self._db.execute(
            "SELECT ticker, rank, score, prob FROM scores "
            "WHERE as_of = ? AND universe = ? ORDER BY rowid",
            (run[0], run[1]),
        ).fetchall()
        left_out = self._db.execute(
            "SELECT ticker, reason FROM left_out "
            "WHERE as_of = ? AND universe = ? ORDER BY rowid",
            (run[0], run[1]),
        ).fetchall()
        return ScoresReport(
            as_of=date.fromisoformat(run[0]),
            universe=run[1],
            price_source=run[2],
            facts_source=run[3],
            horizon_days=run[4],
            model=ModelInfo(train_rows=run[5], holdout_rows=run[6], holdout_auc=run[7]),
            rows=[
                ScoreRow(ticker=t, rank=rank, score=score, prob=prob)
                for t, rank, score, prob in rows
            ],
            left_out=[LeftOut(ticker=t, reason=reason) for t, reason in left_out],
        )
