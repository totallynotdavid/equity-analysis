"""Fills the store from the SMV API, one query window at a time."""

import logging

from typing import TYPE_CHECKING

from bvl import records, smv, store


if TYPE_CHECKING:
    import sqlite3

    from collections.abc import Iterable
    from datetime import date

log = logging.getLogger(__name__)


def fill_quotes(
    client: smv.SmvClient,
    connection: sqlite3.Connection,
    start: date,
    end: date,
    today: date,
) -> int:
    total = 0
    for first, last in smv.windows(start, end):
        rows = [records.quote(r) for r in smv.quotes(client, first, last, today)]
        total += store.write(connection, "quotes", rows)
        log.info("quotes %s..%s: %d rows", first, last, len(rows))
    return total


def fill_events(
    client: smv.SmvClient,
    connection: sqlite3.Connection,
    start: date,
    end: date,
    today: date,
) -> int:
    total = 0
    for first, last in smv.windows(start, end):
        rows = [records.event(r) for r in smv.events(client, first, last, today)]
        total += store.write(connection, "events", rows)
        log.info("events %s..%s: %d rows", first, last, len(rows))
    return total


def fill_index_levels(
    client: smv.SmvClient,
    connection: sqlite3.Connection,
    days: Iterable[date],
    today: date,
) -> int:
    """Fetch index levels for each of `days`, one request a day."""
    total = 0
    for day in days:
        rows = [records.index_level(r) for r in smv.index_levels(client, day, today)]
        total += store.write(connection, "index_levels", rows)
        log.info("index levels %s: %d rows", day, len(rows))
    return total


def missing_index_days(connection: sqlite3.Connection) -> list[str]:
    """Trading days in the quotes table that have no index level yet."""
    found = connection.execute(
        "SELECT DISTINCT date FROM quotes "
        "WHERE date NOT IN (SELECT DISTINCT date FROM index_levels) ORDER BY date"
    ).fetchall()
    return [day for (day,) in found]
