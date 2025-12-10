from fastapi.testclient import TestClient

from llmtrader.app import create_app


def test_status_endpoint() -> None:
    client = TestClient(create_app())

    response = client.get("/status")

    assert response.status_code == 200
    body = response.json()
    assert body["env"] == "local"
    assert "binance_base_url" in body




