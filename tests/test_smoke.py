from fastapi.testclient import TestClient

from ndrchst.api.main import app


def test_healthz():
    client = TestClient(app)
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_platforms_include_bedrock():
    client = TestClient(app)
    r = client.get("/api/platforms")
    assert r.status_code == 200
    ids = {p["id"] for p in r.json()}
    assert "bedrock" in ids
    assert "paper" in ids
