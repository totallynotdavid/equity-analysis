import json
import sqlite3

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient
from index_api.main import app


if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _serve(demo_run: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("INDEX_DB", str(demo_run / "db.sqlite"))
    return demo_run / "scores.json"


def test_scores_return_the_rows_that_eq_run_exported(
    demo_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exported = json.loads(_serve(demo_run, monkeypatch).read_text())

    response = TestClient(app).get("/scores")

    assert response.status_code == 200
    assert response.json() == exported
    assert len(response.json()["rows"]) == 30


def test_health_reports_the_as_of_date_of_the_latest_scores(
    demo_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(demo_run, monkeypatch)

    response = TestClient(app).get("/health")

    assert response.json() == {"status": "ok", "as_of": "2026-09-30"}


def test_scores_are_not_found_before_any_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INDEX_DB", str(tmp_path / "none.sqlite"))

    response = TestClient(app).get("/scores")

    assert response.status_code == 404


def test_a_database_with_an_older_layout_is_unavailable_with_the_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = tmp_path / "old.sqlite"
    with sqlite3.connect(old) as connection:
        connection.execute("CREATE TABLE prices (ticker TEXT, date TEXT, close REAL)")
    connection.close()
    monkeypatch.setenv("INDEX_DB", str(old))

    response = TestClient(app).get("/scores")

    assert response.status_code == 503
    assert "older layout; delete it" in response.json()["detail"]


def test_a_ticker_returns_the_report_that_eq_run_wrote(
    demo_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _serve(demo_run, monkeypatch)
    written = json.loads((out.parent / "tickers" / "MSFT.json").read_text())

    response = TestClient(app).get("/tickers/msft")

    assert response.status_code == 200
    assert response.json() == written
    assert response.json()["series"][0]["points"][0]["parts"][0]["filing"]["url"]


def test_a_ticker_with_no_report_is_not_found(
    demo_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(demo_run, monkeypatch)

    response = TestClient(app).get("/tickers/NOPE")

    assert response.status_code == 404
    assert "NOPE" in response.json()["detail"]


def test_tickers_are_not_found_before_any_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INDEX_DB", str(tmp_path / "none.sqlite"))

    assert TestClient(app).get("/tickers/AAPL").status_code == 404
