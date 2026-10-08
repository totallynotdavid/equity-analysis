import subprocess
import sys

from pathlib import Path

import pytest

from fastapi.testclient import TestClient
from index_api.main import app


@pytest.fixture(autouse=True)
def empty_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INDEX_DB", str(tmp_path / "none.sqlite"))


def test_health_reports_status_and_no_as_of_date_before_any_scores() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "as_of": None}


def test_api_package_is_importable_without_an_app_dir() -> None:
    root = Path(__file__).parents[3]
    result = subprocess.run(
        [sys.executable, "-c", "import index_api.main"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
