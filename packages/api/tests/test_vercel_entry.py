import importlib.util

from pathlib import Path

import pytest

from fastapi.testclient import TestClient


ENTRY = Path(__file__).parents[3] / "api" / "index.py"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("INDEX_DB", str(tmp_path / "none.sqlite"))
    spec = importlib.util.spec_from_file_location("vercel_entry", ENTRY)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return TestClient(module.app)


def test_the_api_answers_under_the_api_prefix(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "as_of": None}


def test_the_api_does_not_answer_outside_the_prefix(client: TestClient) -> None:
    assert client.get("/health").status_code == 404
