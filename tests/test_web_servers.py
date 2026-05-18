"""End-to-end tests for the servers UI through FastAPI TestClient.

Stresses:
  - App boots without Docker; lists 0 servers, shows banner
  - GET / returns full HTML with sidebar + empty state
  - GET /servers/new returns the create-form overlay partial
  - POST /servers without Docker returns 503 with explanatory message
  - With Docker injected, create+list+delete round-trips through htmx
  - HX-Trigger on create fires the servers-changed event
  - Invalid create input re-renders the form with the error inline (422)
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ndrchst.api.deps import AppState
from ndrchst.api.main import create_app
from ndrchst.domain.models import Family
from ndrchst.platforms import REGISTRY as PLATFORMS
from ndrchst.platforms.base import InstallArtifact
from ndrchst.runtime.docker import Docker
from ndrchst.runtime.lifecycle import Lifecycle
from tests.test_docker_runtime import FakeClient


@pytest.fixture
def app_no_docker(tmp_path: Path):
    """App built with the real lifespan; Docker unreachable (no daemon)."""
    return create_app(
        db_path=tmp_path / "t.db",
        servers_root=tmp_path / "servers",
    )


@pytest.fixture
def app_with_docker(tmp_path: Path, monkeypatch):
    """App with a fake Docker injected post-startup."""
    # Stub platform install so create() doesn't hit the network
    for p in PLATFORMS.values():
        async def fake_install(version, dest, *, _p=p):
            dest.mkdir(parents=True, exist_ok=True)
            if _p.family is Family.JAVA:
                (dest / "server.jar").write_bytes(b"fake")
                return InstallArtifact(path=dest, entrypoint="server.jar")
            (dest / "bedrock_server").write_bytes(b"fake")
            return InstallArtifact(path=dest, entrypoint="bedrock_server")
        monkeypatch.setattr(p, "install", fake_install)

    app = create_app(db_path=tmp_path / "t.db", servers_root=tmp_path / "servers")

    # Pre-flight: TestClient triggers lifespan on first request. To override
    # state, we wrap the app in a TestClient using context-manager mode and
    # mutate state after enter.
    return app


def test_index_renders_without_docker(app_no_docker):
    with TestClient(app_no_docker) as client:
        r = client.get("/")
        assert r.status_code == 200
        html = r.text
        assert "ndrchst" in html
        assert "No servers yet" in html
        # Docker-unavailable banner present
        assert "running in read-only mode" in html
        # Create button is disabled
        assert "disabled" in html


def test_healthz_reports_docker_unavailable(app_no_docker):
    with TestClient(app_no_docker) as client:
        r = client.get("/healthz")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["docker"] == "unavailable"
        assert body["docker_error"]  # non-empty


def test_new_form_returns_overlay_partial(app_no_docker):
    with TestClient(app_no_docker) as client:
        r = client.get("/servers/new", headers={"HX-Request": "true"})
        assert r.status_code == 200
        html = r.text
        assert 'class="overlay"' in html
        # All 7 platforms appear in the dropdown, Bedrock included
        for pid in ("paper", "purpur", "vanilla", "fabric", "forge", "neoforge", "bedrock"):
            assert f'value="{pid}"' in html


def test_create_returns_503_without_docker(app_no_docker):
    with TestClient(app_no_docker) as client:
        r = client.post("/servers", data={
            "name": "X", "platform_id": "paper", "version": "1.21.3",
            "port": "25565", "memory_mb": "2048",
        })
        assert r.status_code == 503
        assert "Docker" in r.text


def test_api_servers_list_initially_empty(app_no_docker):
    with TestClient(app_no_docker) as client:
        r = client.get("/api/servers")
        assert r.status_code == 200
        assert r.json() == []


def test_create_validates_via_api(app_with_docker):
    """The JSON API path; with FakeClient injected after lifespan."""
    with TestClient(app_with_docker) as client:
        # Replace lifecycle with one wired to FakeClient
        st: AppState = app_with_docker.state.ndrchst
        st.lifecycle = Lifecycle(
            Docker(client=FakeClient()), st.conn,
            servers_root=Path(app_with_docker.state.ndrchst.conn.execute(
                "SELECT 'placeholder'"
            ).fetchone()[0])  # dummy expression, we set the real root below
        )
        # Actually we want the real root; build a fresh lifecycle
        import tempfile
        root = Path(tempfile.mkdtemp())
        st.lifecycle = Lifecycle(Docker(client=FakeClient()), st.conn, servers_root=root)

        # Invalid: bad name
        r = client.post("/api/servers", json={
            "name": "../escape", "platform_id": "paper", "version": "1.21.3",
            "port": 25565, "memory_mb": 2048,
        })
        assert r.status_code == 400
        assert "server name" in r.text

        # Valid create
        r = client.post("/api/servers", json={
            "name": "Survival", "platform_id": "paper", "version": "1.21.3",
            "port": 25565, "memory_mb": 2048,
        })
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "Survival"
        assert body["family"] == "java"
        sid = body["id"]

        # Lists includes it
        r = client.get("/api/servers")
        assert any(s["id"] == sid for s in r.json())

        # Delete
        r = client.delete(f"/api/servers/{sid}")
        assert r.status_code == 204

        r = client.get("/api/servers")
        assert all(s["id"] != sid for s in r.json())


def test_html_create_round_trip(app_with_docker):
    """Full htmx flow: POST /servers with form, then refresh fragment."""
    with TestClient(app_with_docker) as client:
        # Inject fake docker lifecycle
        st: AppState = app_with_docker.state.ndrchst
        import tempfile
        root = Path(tempfile.mkdtemp())
        st.lifecycle = Lifecycle(Docker(client=FakeClient()), st.conn, servers_root=root)

        r = client.post("/servers", data={
            "name": "FromForm",
            "platform_id": "bedrock",
            "version": "latest",
            "port": "19132",
            "memory_mb": "1024",
        }, headers={"HX-Request": "true"})
        assert r.status_code == 200
        # The create handler signals the list to refresh
        assert r.headers.get("HX-Trigger") == "ndrchst:servers-changed"

        # The list fragment now contains the new server
        r = client.get("/servers/list", headers={"HX-Request": "true"})
        assert r.status_code == 200
        assert "FromForm" in r.text
        assert "bedrock latest" in r.text
        assert ":19132" in r.text


def test_html_create_invalid_renders_inline_error(app_with_docker):
    with TestClient(app_with_docker) as client:
        st: AppState = app_with_docker.state.ndrchst
        import tempfile
        root = Path(tempfile.mkdtemp())
        st.lifecycle = Lifecycle(Docker(client=FakeClient()), st.conn, servers_root=root)

        # Privileged port
        r = client.post("/servers", data={
            "name": "Bad", "platform_id": "paper", "version": "1.21.3",
            "port": "80", "memory_mb": "2048",
        }, headers={"HX-Request": "true"})
        assert r.status_code == 422
        assert "port must be" in r.text
        # Form is re-rendered so the user can fix and retry
        assert 'class="overlay"' in r.text
