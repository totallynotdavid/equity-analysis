import json

from contextlib import closing
from pathlib import Path

from bvl import records, store
from bvl.smv import parse_envelope


FIXTURES = Path(__file__).parent / "fixtures"


def rows(name: str) -> list[dict[str, object]]:
    return parse_envelope(json.loads((FIXTURES / name).read_text()))


def test_quote_row_has_iso_dates_and_prices() -> None:
    credit = next(r for r in rows("quotes.json") if r["Valor"] == "CREDITC1")
    row = records.quote(credit)
    assert row["date"] == "2025-11-07"
    assert row["prev_date"] == "2025-11-06"
    assert row["ticker"] == "CREDITC1"
    assert (row["prev_close"], row["close"]) == (4.6, 4.58)


def test_event_row_keeps_the_filing_minute() -> None:
    row = records.event(rows("events.json")[0])
    assert row["filed_at"] == "2026-10-30T16:45"
    assert row["agreed_on"] == "2026-10-30"
    assert row["ruc"] == "20101045995"


def test_index_row_reads_the_level() -> None:
    row = records.index_level(rows("indices.json")[0])
    assert (row["date"], row["code"], row["close"]) == ("2005-01-03", "IGBVL", 3774.37)


def test_refetching_a_span_replaces_rows(tmp_path: Path) -> None:
    db = tmp_path / "bvl.sqlite"
    with closing(store.connect(db)) as connection:
        for _ in range(2):
            store.write(connection, "quotes", map(records.quote, rows("quotes.json")))
            store.write(connection, "events", map(records.event, rows("events.json")))
    quotes = store.read(db, "SELECT * FROM quotes")
    events = store.read(db, "SELECT * FROM events")
    assert len(quotes) == 3
    # The fixture repeats one (filing, kind) pair, as the API does.
    assert len(events) == len(rows("events.json")) - 1
