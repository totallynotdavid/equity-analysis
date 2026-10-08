import json

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient
from index_api.main import app
from index_cli.main import main


if TYPE_CHECKING:
    import pytest


DEMO = Path(__file__).parents[3] / "universes" / "demo30.txt"


def _run_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    database = tmp_path / "db.sqlite"
    out = tmp_path / "scores.json"
    main(
        [
            *["run", "--universe", str(DEMO)],
            *["--prices", "synthetic", "--filings", "synthetic"],
            *["--start", "2024-01-01", "--end", "2026-09-30"],
            *["--db", str(database), "--out", str(out)],
        ]
    )
    monkeypatch.setenv("INDEX_DB", str(database))
    return out


def test_scores_return_the_rows_that_eq_run_exported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exported = json.loads(_run_cli(tmp_path, monkeypatch).read_text())

    response = TestClient(app).get("/scores")

    assert response.status_code == 200
    assert response.json() == exported
    assert len(response.json()["rows"]) == 30


def test_health_reports_the_as_of_date_of_the_latest_scores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run_cli(tmp_path, monkeypatch)

    response = TestClient(app).get("/health")

    assert response.json() == {"status": "ok", "as_of": "2026-09-30"}


def test_scores_are_not_found_before_any_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INDEX_DB", str(tmp_path / "none.sqlite"))

    response = TestClient(app).get("/scores")

    assert response.status_code == 404
