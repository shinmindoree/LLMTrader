from fastapi.testclient import TestClient

from llmtrader.app import create_app


def test_healthcheck() -> None:
    client = TestClient(create_app())

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}




