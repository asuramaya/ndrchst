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


def test_platforms_default_hides_bedrock(client: TestClient):
    """Bedrock stays registered (code intact for future OSS / re-enable)
    but is hidden from the default API listing — the product is focused
    on modded Java right now."""
    r = client.get("/api/platforms")
    assert r.status_code == 200
    ids = {p["id"] for p in r.json()}
    assert "paper" in ids
    assert "bedrock" not in ids


def test_platforms_include_hidden_flag_surfaces_bedrock(client: TestClient):
    """The hidden platform shows up when explicitly opted in."""
    r = client.get("/api/platforms?include_hidden=true")
    assert r.status_code == 200
    payload = r.json()
    ids = {p["id"] for p in payload}
    assert "bedrock" in ids
    bedrock = next(p for p in payload if p["id"] == "bedrock")
    assert bedrock["default_visible"] is False
    # Paper is still visible by default
    paper = next(p for p in payload if p["id"] == "paper")
    assert paper["default_visible"] is True


def test_platforms_surface_default_memory(client: TestClient):
    """Modded platforms need a higher RAM default than Paper. The API
    surfaces this so the create form can pre-fill the right number."""
    r = client.get("/api/platforms")
    payload = r.json()
    by_id = {p["id"]: p for p in payload}
    assert by_id["paper"]["default_memory_mb"] == 2048
    assert by_id["neoforge"]["default_memory_mb"] == 8192
    assert by_id["modpack"]["default_memory_mb"] == 8192
