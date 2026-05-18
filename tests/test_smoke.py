"""Smoke tests — assert the app boots and exposes its expected surface."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ndrchst.api.main import create_app


@pytest.fixture
def client(tmp_path: Path):
    app = create_app(db_path=tmp_path / "smoke.db", servers_root=tmp_path / "servers")
    with TestClient(app) as c:
        yield c


def test_healthz(client: TestClient):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "docker" in body
    assert "version" in body


def test_platforms_include_bedrock(client: TestClient):
    r = client.get("/api/platforms")
    assert r.status_code == 200
    ids = {p["id"] for p in r.json()}
    assert "bedrock" in ids
    assert "paper" in ids
