from equity_analyzer_api.main import app
from fastapi.testclient import TestClient


def test_health_reports_status_and_no_as_of_date_yet() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "as_of": None}
