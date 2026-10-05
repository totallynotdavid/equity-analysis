from typing import TYPE_CHECKING

import pytest

from fastapi.testclient import TestClient
from index_api.main import app


if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def empty_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INDEX_DB", str(tmp_path / "none.sqlite"))


def test_health_reports_status_and_no_as_of_date_before_any_scores() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "as_of": None}
