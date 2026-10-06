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
    return Path(os.environ.get(DB_VARIABLE, "data/index.sqlite"))


class Store:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    @classmethod
    def open(cls, path: Path, *, read_only: bool = False) -> Self:
        if read_only:
            connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(path)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(_SCHEMA)
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

    def upsert_prices(self, source: str, ticker: str, bars: pd.DataFrame) -> None:
        """Store bars, replacing any stored for the same ticker and date.

        One database holds prices from one source. Mixing, say, synthetic and
        real bars would make every score downstream meaningless.
        """
        with self._db:
            stored = self._db.execute(
                "SELECT value FROM meta WHERE key = 'price_source'"
            ).fetchone()
            if stored is not None and stored[0] != source:
                raise StoreError(
                    f"this database holds {stored[0]} prices; use a new database "
                    f"for {source}"
                )
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
                    report.source,
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
            "SELECT as_of, universe, source, horizon_days, train_rows, "
            "holdout_rows, holdout_auc FROM runs "
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
            source=run[2],
            horizon_days=run[3],
            model=ModelInfo(train_rows=run[4], holdout_rows=run[5], holdout_auc=run[6]),
            rows=[
                ScoreRow(ticker=t, rank=rank, score=score, prob=prob)
                for t, rank, score, prob in rows
            ],
        )
