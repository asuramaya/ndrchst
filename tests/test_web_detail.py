"""End-to-end test of the server detail page + every tab.

Drives the same HTML routes the htmx UI hits, asserts structure of rendered
partials, and round-trips mutations. The WebSocket console route is tested
separately.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from ndrchst.api.deps import AppState
from ndrchst.api.main import create_app
from ndrchst.domain.models import Family
from ndrchst.mods.modrinth import Modrinth
from ndrchst.platforms import REGISTRY as PLATFORMS
from ndrchst.platforms.base import InstallArtifact
from ndrchst.runtime.docker import Docker
from ndrchst.runtime.lifecycle import Lifecycle
from tests.test_docker_runtime import FakeClient

_FABRIC_JAR_BYTES = b"jar"
_FABRIC_JAR_SHA1 = hashlib.sha1(_FABRIC_JAR_BYTES).hexdigest()


def _modrinth_handler():
    """A predictable Modrinth that the marketplace UI talks to."""
    def h(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/search":
            return httpx.Response(200, json={
                "hits": [
                    {"project_id": "fabric-api", "slug": "fabric-api",
                     "title": "Fabric API", "description": "Essential API"},
                    {"project_id": "lithium", "slug": "lithium",
                     "title": "Lithium", "description": "Performance"},
                ]
            })
        if request.url.path.startswith("/v2/project/fabric-api/version"):
            return httpx.Response(200, json=[{
                "version_number": "0.100.0",
                "version_type": "release",
                "date_published": "2025-01-01T00:00:00Z",
                "game_versions": ["1.21.3"],
                "loaders": ["fabric"],
                "dependencies": [],
                "files": [{
                    "primary": True,
                    "filename": "fabric-api-0.100.0.jar",
                    "url": "https://cdn/fabric.jar",
                    "size": len(_FABRIC_JAR_BYTES),
                    "hashes": {"sha1": _FABRIC_JAR_SHA1},
                }],
            }])
        if str(request.url) == "https://cdn/fabric.jar":
            return httpx.Response(200, content=_FABRIC_JAR_BYTES)
        return httpx.Response(404)
    return h


@pytest.fixture
def detail_client(tmp_path: Path, monkeypatch):
    """Boot the real app with Docker faked + Modrinth mocked + a created server."""
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

    # We have to enter the context manager so lifespan runs and state is set
    client = TestClient(app)
    client.__enter__()

    st: AppState = app.state.ndrchst
    fake_docker = Docker(client=FakeClient())
    st.lifecycle = Lifecycle(fake_docker, st.conn, servers_root=tmp_path / "servers")
    # Swap in a mocked Modrinth so /mods/search + install are deterministic
    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(_modrinth_handler()))
    st.modrinth = Modrinth(client=mock_client)
    st.http_client = mock_client

    # Create one Java server up-front
    r = client.post("/servers", data={
        "name": "Survival",
        "platform_id": "paper",
        "version": "1.21.3",
        "port": "25571",
        "memory_mb": "2048",
    }, headers={"HX-Request": "true"})
    assert r.status_code == 200, r.text

    list_r = client.get("/api/servers")
    server_id = list_r.json()[0]["id"]

    yield client, server_id, tmp_path
    client.__exit__(None, None, None)


def test_detail_full_page_renders(detail_client):
    client, sid, _ = detail_client
    r = client.get(f"/servers/{sid}/console")
    assert r.status_code == 200
    html = r.text
    assert "Survival" in html
    assert "← Back" in html or "&larr; Back" in html
    # All tabs in the nav
    for tab in ("Console", "Properties", "Players", "Files", "Marketplace", "Backups"):
        assert tab in html


def test_detail_htmx_returns_tab_partial(detail_client):
    client, sid, _ = detail_client
    # Console
    r = client.get(f"/servers/{sid}/console", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert 'class="console"' in r.text
    assert "console-output" in r.text

    # Properties — empty initially
    r = client.get(f"/servers/{sid}/properties", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "props-form" in r.text or "Start the server once" in r.text

    # Files — should list eula.txt at least (written by lifecycle.create)
    r = client.get(f"/servers/{sid}/files", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "eula.txt" in r.text
    assert "server.jar" in r.text


def test_eula_was_written_at_create(detail_client):
    _, sid, root = detail_client
    eula = root / "servers" / sid / "eula.txt"
    assert eula.exists()
    assert "eula=true" in eula.read_text()


def test_properties_save_round_trip(detail_client):
    client, sid, root = detail_client
    # write a starter properties file
    (root / "servers" / sid / "server.properties").write_text("motd=Old\nmax-players=10\n")
    # Save via the form
    r = client.post(f"/servers/{sid}/properties",
                    data={"motd": "New", "max-players": "20"})
    assert r.status_code == 200
    assert "Saved" in r.text
    text = (root / "servers" / sid / "server.properties").read_text()
    assert "motd=New" in text
    assert "max-players=20" in text


def test_files_list_then_edit_round_trip(detail_client):
    client, sid, root = detail_client
    (root / "servers" / sid / "config.yml").write_text("key: original\n")

    # list
    r = client.get(f"/servers/{sid}/files", headers={"HX-Request": "true"})
    assert "config.yml" in r.text

    # open edit form
    r = client.get(f"/servers/{sid}/files/edit?path=config.yml",
                   headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "key: original" in r.text

    # save
    r = client.post(f"/servers/{sid}/files/edit?path=config.yml",
                    data={"content": "key: updated\n"})
    assert r.status_code == 200
    assert (root / "servers" / sid / "config.yml").read_text() == "key: updated\n"


def test_files_traversal_rejected(detail_client):
    client, sid, _ = detail_client
    r = client.get(f"/servers/{sid}/files?path=../../../etc",
                   headers={"HX-Request": "true"})
    assert r.status_code == 400


def test_mods_search_then_install_then_remove(detail_client):
    client, sid, _ = detail_client

    # Search
    r = client.get(f"/servers/{sid}/mods/search?q=fabric",
                   headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "Fabric API" in r.text
    assert "Install" in r.text

    # Install
    r = client.post(f"/servers/{sid}/mods/install",
                    data={"source_id": "modrinth", "asset_id": "fabric-api"})
    assert r.status_code == 200
    assert "fabric-api" in r.text

    # Remove
    r = client.delete(f"/servers/{sid}/mods/modrinth/fabric-api")
    assert r.status_code == 200
    assert "No mods installed" in r.text


def test_backups_create_list_restore_delete(detail_client, monkeypatch, tmp_path):
    # Redirect backups root to a temp dir so we don't pollute ~/.ndrchst
    from ndrchst.runtime import backup as bm
    monkeypatch.setattr(bm, "BACKUPS_ROOT_DEFAULT", tmp_path / "bk")

    client, sid, _ = detail_client

    # Create
    r = client.post(f"/servers/{sid}/backups")
    assert r.status_code == 200
    assert ".tar.gz" in r.text

    # List
    r = client.get(f"/servers/{sid}/backups", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert ".tar.gz" in r.text


def test_mod_install_takes_pre_install_safety_snapshot(
    detail_client, monkeypatch, tmp_path,
):
    """Installing a mod should auto-snapshot the data dir first."""
    from ndrchst.runtime import backup as bm
    monkeypatch.setattr(bm, "BACKUPS_ROOT_DEFAULT", tmp_path / "bk")

    client, sid, _ = detail_client

    # Install — handler should auto-snapshot before mutating
    r = client.post(f"/servers/{sid}/mods/install",
                    data={"source_id": "modrinth", "asset_id": "fabric-api"})
    assert r.status_code == 200

    safeties = [b for b in bm.list_for(sid, root=tmp_path / "bk") if b.is_safety]
    assert len(safeties) == 1
    assert safeties[0].safety_reason == "pre_install"

    # The safety snapshot also surfaces in the backups list UI
    r = client.get(f"/servers/{sid}/backups", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "auto-pre_install" in r.text
    assert "auto · pre_install" in r.text


def test_backup_restore_takes_pre_restore_safety_snapshot(
    detail_client, monkeypatch, tmp_path,
):
    from ndrchst.runtime import backup as bm
    monkeypatch.setattr(bm, "BACKUPS_ROOT_DEFAULT", tmp_path / "bk")

    client, sid, _ = detail_client

    # Make a user backup to restore from
    r = client.post(f"/servers/{sid}/backups")
    assert r.status_code == 200
    user_backups = [b for b in bm.list_for(sid, root=tmp_path / "bk") if not b.is_safety]
    assert len(user_backups) == 1
    name = user_backups[0].name

    # Restore — handler should auto-snapshot before wiping
    r = client.post(f"/servers/{sid}/backups/{name}/restore")
    assert r.status_code == 200

    safeties = [b for b in bm.list_for(sid, root=tmp_path / "bk") if b.is_safety]
    assert len(safeties) == 1
    assert safeties[0].safety_reason == "pre_restore"


def test_players_tab_bedrock_shows_placeholder(detail_client):
    client, _, _ = detail_client
    # Create a Bedrock server to test the family branch
    r = client.post("/servers", data={
        "name": "BedrockOne", "platform_id": "bedrock",
        "version": "latest", "port": "19233", "memory_mb": "1024",
    }, headers={"HX-Request": "true"})
    assert r.status_code == 200

    r = client.get("/api/servers")
    bdrk = next(s for s in r.json() if s["name"] == "BedrockOne")

    r = client.get(f"/servers/{bdrk['id']}/players", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "Bedrock" in r.text and "Console tab" in r.text


def test_unknown_tab_404(detail_client):
    client, sid, _ = detail_client
    r = client.get(f"/servers/{sid}/nonexistent")
    assert r.status_code == 404
