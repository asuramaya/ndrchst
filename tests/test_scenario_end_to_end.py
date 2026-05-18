"""End-to-end UI tests against a realistically-seeded server data dir.

Exercises the worlds tab (real NBT), backup of a realistic tree, file
browser traversal of the world subtree, and properties editing against
a populated server.properties.

This is the closest thing we have to "run it for real" without Docker.
"""
from __future__ import annotations

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
from tests.scenario import seed_server_dir
from tests.test_docker_runtime import FakeClient


@pytest.fixture
def seeded(tmp_path: Path, monkeypatch):
    """App with one realistically-populated Java server created out-of-band."""
    for p in PLATFORMS.values():
        async def fake_install(version, dest, *, _p=p):
            # Use the scenario builder so the server starts life with a real
            # level.dat, populated plugins/, mods/, etc.
            if _p.family is Family.JAVA:
                seed_server_dir(dest)
            else:
                dest.mkdir(parents=True, exist_ok=True)
                (dest / "bedrock_server").write_bytes(b"fake")
            return InstallArtifact(
                path=dest,
                entrypoint="server.jar" if _p.family is Family.JAVA else "bedrock_server",
            )
        monkeypatch.setattr(p, "install", fake_install)

    app = create_app(db_path=tmp_path / "t.db", servers_root=tmp_path / "servers")
    client = TestClient(app)
    client.__enter__()

    st: AppState = app.state.ndrchst
    st.lifecycle = Lifecycle(Docker(client=FakeClient()), st.conn, servers_root=tmp_path / "servers")
    # Modrinth not exercised here; left at default
    st.modrinth = Modrinth(client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))))

    r = client.post("/servers", data={
        "name": "Realistic",
        "platform_id": "paper",
        "version": "1.21.3",
        "port": "25571",
        "memory_mb": "2048",
    }, headers={"HX-Request": "true"})
    assert r.status_code == 200, r.text

    sid = client.get("/api/servers").json()[0]["id"]
    yield client, sid, tmp_path / "servers" / sid
    client.__exit__(None, None, None)


def test_worlds_tab_renders_real_nbt(seeded):
    client, sid, _ = seeded
    r = client.get(f"/servers/{sid}/worlds", headers={"HX-Request": "true"})
    assert r.status_code == 200
    html = r.text
    # Values from scenario.make_level_dat defaults
    assert "7331" in html  # seed
    assert "World" in html  # level name
    assert "keepInventory" in html
    assert "doDaylightCycle" in html


def test_worlds_full_page_includes_tab_nav(seeded):
    client, sid, _ = seeded
    r = client.get(f"/servers/{sid}/worlds")
    assert r.status_code == 200
    # All 7 tabs present in nav (now includes worlds)
    for label in ("Console", "Properties", "Players", "World", "Files", "Marketplace", "Backups"):
        assert label in r.text


def test_worlds_gamerule_edit_persists(seeded):
    client, sid, _ = seeded
    r = client.post(f"/servers/{sid}/worlds/gamerules", data={
        "keepInventory": "true",
        "doDaylightCycle": "true",
        "doMobSpawning": "false",
    })
    assert r.status_code == 200
    assert "Saved" in r.text

    # Re-read via the same endpoint
    r = client.get(f"/servers/{sid}/worlds", headers={"HX-Request": "true"})
    # The form fields should reflect the new value
    assert 'name="keepInventory" value="true"' in r.text
    assert 'name="doMobSpawning" value="false"' in r.text


def test_files_browser_shows_realistic_tree(seeded):
    client, sid, _ = seeded
    r = client.get(f"/servers/{sid}/files", headers={"HX-Request": "true"})
    assert r.status_code == 200
    html = r.text
    # Top-level entries the scenario seeded
    for name in ("plugins", "mods", "world", "server.properties", "eula.txt", "ops.json"):
        assert name in html


def test_files_browser_descends_into_world_subtree(seeded):
    client, sid, _ = seeded
    r = client.get(f"/servers/{sid}/files?path=world",
                   headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "level.dat" in r.text
    assert "region" in r.text


def test_backup_of_realistic_tree_round_trips(seeded, monkeypatch, tmp_path):
    """Backup → mutate → restore should bring back exact bytes."""
    from ndrchst.runtime import backup as bm
    monkeypatch.setattr(bm, "BACKUPS_ROOT_DEFAULT", tmp_path / "bk")

    client, sid, data_dir = seeded
    eula_before = (data_dir / "eula.txt").read_bytes()
    level_before = (data_dir / "world" / "level.dat").read_bytes()

    # Backup
    r = client.post(f"/servers/{sid}/backups")
    assert r.status_code == 200

    # Mutate
    (data_dir / "eula.txt").write_text("eula=false\n")
    (data_dir / "world" / "level.dat").write_bytes(b"corrupted")
    (data_dir / "extra-file").write_text("added after backup")

    # Restore via UI — find the backup name
    r = client.get(f"/servers/{sid}/backups", headers={"HX-Request": "true"})
    import re
    match = re.search(r"(\d{8}T\d{6}Z\.tar\.gz)", r.text)
    assert match
    name = match.group(1)

    r = client.post(f"/servers/{sid}/backups/{name}/restore")
    assert r.status_code == 200

    assert (data_dir / "eula.txt").read_bytes() == eula_before
    assert (data_dir / "world" / "level.dat").read_bytes() == level_before
    assert not (data_dir / "extra-file").exists()


def test_properties_save_against_realistic_file(seeded):
    client, sid, data_dir = seeded  # data_dir used to read written props back
    # Scenario lays down a real server.properties; flip motd
    r = client.post(f"/servers/{sid}/properties", data={
        "motd": "Edited by ndrchst",
        "level-name": "world",  # keep existing
        "server-port": "25565",
        "max-players": "100",  # changed
        "online-mode": "true",
        "enable-rcon": "false",
    })
    assert r.status_code == 200
    text = (data_dir / "server.properties").read_text()
    assert "motd=Edited by ndrchst" in text
    assert "max-players=100" in text


def test_bedrock_worlds_tab_shows_placeholder(seeded):
    client, _, _ = seeded
    # Make a Bedrock server too
    r = client.post("/servers", data={
        "name": "BdrkOne", "platform_id": "bedrock",
        "version": "latest", "port": "19234", "memory_mb": "1024",
    }, headers={"HX-Request": "true"})
    assert r.status_code == 200

    bedrock_sid = next(s["id"] for s in client.get("/api/servers").json() if s["name"] == "BdrkOne")
    r = client.get(f"/servers/{bedrock_sid}/worlds", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "Bedrock" in r.text and "LevelDB" in r.text
