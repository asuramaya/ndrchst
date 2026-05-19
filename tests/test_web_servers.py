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
def app_no_docker(tmp_path: Path, monkeypatch):
    """App built with the real lifespan; Docker forced unreachable.

    Stubs ``docker.from_env`` so the test outcome is deterministic on hosts
    that have Docker available too (e.g. CI, the staging VM).
    """
    import docker as docker_mod
    def _raise(*a, **kw):
        raise docker_mod.errors.DockerException("forced unavailable for test")
    monkeypatch.setattr(docker_mod, "from_env", _raise)
    return create_app(
        db_path=tmp_path / "t.db",
        servers_root=tmp_path / "servers",
    )


@pytest.fixture
def app_with_docker(tmp_path: Path, monkeypatch):
    """App with a fake Docker injected post-startup."""
    # Stub platform install + versions so create() doesn't hit the network.
    from ndrchst.platforms.base import VersionInfo
    for p in PLATFORMS.values():
        async def fake_install(version, dest, *, _p=p):
            dest.mkdir(parents=True, exist_ok=True)
            if _p.family is Family.JAVA:
                (dest / "server.jar").write_bytes(b"fake")
                return InstallArtifact(path=dest, entrypoint="server.jar")
            (dest / "bedrock_server").write_bytes(b"fake")
            return InstallArtifact(path=dest, entrypoint="bedrock_server")
        async def fake_versions():
            return [VersionInfo(version="1.21.3")]
        monkeypatch.setattr(p, "install", fake_install)
        monkeypatch.setattr(p, "versions", fake_versions)

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
        # Only implemented platforms are offered; stubs (purpur/vanilla/fabric/
        # forge/neoforge) would 4xx with NotImplementedError if selected, so
        # we hide them. Bedrock must be present (first-class per project policy).
        for pid in ("paper", "bedrock"):
            assert f'value="{pid}"' in html
        for stub in ("purpur", "vanilla", "fabric", "forge", "neoforge"):
            assert f'value="{stub}"' not in html


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

        # The list fragment now contains the new server. "latest" resolves
        # to the concrete version reported by Platform.versions() — the
        # fixture stubs that to "1.21.3".
        r = client.get("/servers/list", headers={"HX-Request": "true"})
        assert r.status_code == 200
        assert "FromForm" in r.text
        assert "bedrock 1.21.3" in r.text
        assert ":19132" in r.text


def test_assets_page_empty_when_no_servers(app_no_docker):
    with TestClient(app_no_docker) as client:
        r = client.get("/assets")
        assert r.status_code == 200
        assert "Installed assets" in r.text
        assert "No servers yet" in r.text


def test_assets_page_lists_zero_assets_for_servers_with_none(app_with_docker):
    with TestClient(app_with_docker) as client:
        st: AppState = app_with_docker.state.ndrchst
        import tempfile
        root = Path(tempfile.mkdtemp())
        st.lifecycle = Lifecycle(Docker(client=FakeClient()), st.conn, servers_root=root)

        client.post("/servers", data={
            "name": "Empty", "platform_id": "paper", "version": "1.21.3",
            "port": "25700", "memory_mb": "2048",
        }, headers={"HX-Request": "true"})

        r = client.get("/assets")
        assert r.status_code == 200
        assert "Empty" in r.text
        assert "No mods, plugins, or packs installed" in r.text
        assert "0 assets across 1 server" in r.text


def test_assets_page_groups_by_server_and_lists_assets(app_with_docker):
    """When installed_assets rows exist, the global view groups them by server."""
    with TestClient(app_with_docker) as client:
        st: AppState = app_with_docker.state.ndrchst
        import tempfile
        root = Path(tempfile.mkdtemp())
        st.lifecycle = Lifecycle(Docker(client=FakeClient()), st.conn, servers_root=root)

        # Two servers, two assets on the first
        r = client.post("/servers", data={
            "name": "Survival", "platform_id": "paper", "version": "1.21.3",
            "port": "25571", "memory_mb": "2048",
        }, headers={"HX-Request": "true"})
        assert r.status_code == 200
        r = client.post("/servers", data={
            "name": "Bdrk", "platform_id": "bedrock", "version": "latest",
            "port": "19132", "memory_mb": "1024",
        }, headers={"HX-Request": "true"})
        assert r.status_code == 200

        ids = {s["name"]: s["id"] for s in client.get("/api/servers").json()}

        # Insert assets straight into the DB — the installer is exercised
        # elsewhere; here we just verify the view reads + groups correctly.
        st.conn.execute(
            "INSERT INTO installed_assets (server_id, source_id, asset_id, kind, version) "
            "VALUES (?, 'modrinth', 'fabric-api', 'mod', '0.100.0')",
            (ids["Survival"],),
        )
        st.conn.execute(
            "INSERT INTO installed_assets (server_id, source_id, asset_id, kind, version) "
            "VALUES (?, 'modrinth', 'lithium', 'mod', '0.12.0')",
            (ids["Survival"],),
        )

        r = client.get("/assets")
        assert r.status_code == 200
        body = r.text
        assert "Survival" in body
        assert "Bdrk" in body  # listed even with no assets
        assert "fabric-api" in body
        assert "lithium" in body
        assert "0.100.0" in body
        assert "modrinth" in body
        assert "2 assets across 2 servers" in body


def test_assets_appears_in_sidebar(app_no_docker):
    with TestClient(app_no_docker) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert 'href="/assets"' in r.text
        assert ">Assets<" in r.text


# ─── Management functions ported from v2 (versions, restart, stats, rename) ──


def test_versions_endpoint_returns_platform_versions(app_with_docker):
    """GET /api/platforms/paper/versions reflects what platform.versions()
    returns (stubbed in fixture to a single entry)."""
    with TestClient(app_with_docker) as client:
        r = client.get("/api/platforms/paper/versions")
        assert r.status_code == 200
        body = r.json()
        assert any(v["version"] == "1.21.3" for v in body)


def test_versions_endpoint_404_for_unknown_platform(app_with_docker):
    with TestClient(app_with_docker) as client:
        r = client.get("/api/platforms/madeup/versions")
        assert r.status_code == 404


def test_versions_endpoint_400_for_stub_platform(app_with_docker):
    """Stub platforms (purpur/vanilla/etc.) reject with a clean 4xx."""
    with TestClient(app_with_docker) as client:
        # Restore the real (stub) implementation for vanilla
        from ndrchst.platforms import REGISTRY
        # The fixture replaced .versions with a stub; reach the real path by
        # asking a stub directly through the API
        # The fixture's monkeypatch is per-test scope so this works:
        async def stub():
            raise NotImplementedError
        REGISTRY["vanilla"].versions = stub  # type: ignore
        # vanilla.implemented = False, so we should get 400 before .versions runs
        r = client.get("/api/platforms/vanilla/versions")
        assert r.status_code == 400


def test_new_form_versions_fragment_returns_datalist_options(app_with_docker):
    """The htmx fragment endpoint returns <option> tags for the version
    datalist. Used by the create form when platform changes."""
    with TestClient(app_with_docker) as client:
        r = client.get("/servers/new/versions?platform_id=paper")
        assert r.status_code == 200
        assert "<option" in r.text
        assert 'value="latest"' in r.text
        assert "1.21.3" in r.text


def test_restart_endpoint_returns_card_with_running_state(app_with_docker):
    with TestClient(app_with_docker) as client:
        st: AppState = app_with_docker.state.ndrchst
        import tempfile
        root = Path(tempfile.mkdtemp())
        st.lifecycle = Lifecycle(Docker(client=FakeClient()), st.conn, servers_root=root)

        client.post("/servers", data={
            "name": "RestartMe", "platform_id": "paper",
            "version": "1.21.3", "port": "25700", "memory_mb": "2048",
        }, headers={"HX-Request": "true"})
        sid = next(s["id"] for s in client.get("/api/servers").json() if s["name"] == "RestartMe")

        client.post(f"/servers/{sid}/start", headers={"HX-Request": "true"})
        r = client.post(f"/servers/{sid}/restart", headers={"HX-Request": "true"})
        assert r.status_code == 200
        assert "RestartMe" in r.text
        assert "Restart" in r.text   # button visible while running


def test_rename_updates_db_and_returns_new_card(app_with_docker):
    with TestClient(app_with_docker) as client:
        st: AppState = app_with_docker.state.ndrchst
        import tempfile
        root = Path(tempfile.mkdtemp())
        st.lifecycle = Lifecycle(Docker(client=FakeClient()), st.conn, servers_root=root)

        client.post("/servers", data={
            "name": "OldName", "platform_id": "paper",
            "version": "1.21.3", "port": "25701", "memory_mb": "2048",
        }, headers={"HX-Request": "true"})
        sid = next(s["id"] for s in client.get("/api/servers").json() if s["name"] == "OldName")

        r = client.put(f"/servers/{sid}/name", data={"name": "NewName"})
        assert r.status_code == 200
        assert "NewName" in r.text
        assert "OldName" not in r.text
        # DB also reflects it
        assert any(s["name"] == "NewName" for s in client.get("/api/servers").json())


def test_rename_rejects_bad_name(app_with_docker):
    with TestClient(app_with_docker) as client:
        st: AppState = app_with_docker.state.ndrchst
        import tempfile
        root = Path(tempfile.mkdtemp())
        st.lifecycle = Lifecycle(Docker(client=FakeClient()), st.conn, servers_root=root)

        client.post("/servers", data={
            "name": "Original", "platform_id": "paper",
            "version": "1.21.3", "port": "25702", "memory_mb": "2048",
        }, headers={"HX-Request": "true"})
        sid = next(s["id"] for s in client.get("/api/servers").json() if s["name"] == "Original")

        r = client.put(f"/servers/{sid}/name", data={"name": "../escape"})
        assert r.status_code == 400


def test_stats_fragment_returns_cpu_and_memory_html(app_with_docker):
    with TestClient(app_with_docker) as client:
        st: AppState = app_with_docker.state.ndrchst
        import tempfile
        root = Path(tempfile.mkdtemp())
        st.lifecycle = Lifecycle(Docker(client=FakeClient()), st.conn, servers_root=root)

        # FakeClient doesn't stub stats(); patch the lifecycle method directly
        from ndrchst.runtime.docker import ContainerStats

        async def fake_stats(self, sid):
            return ContainerStats(cpu_percent=12.5, memory_used_mb=512, memory_limit_mb=2048)

        import ndrchst.runtime.lifecycle as lc
        original = lc.Lifecycle.stats
        lc.Lifecycle.stats = fake_stats
        try:
            client.post("/servers", data={
                "name": "Stats", "platform_id": "paper",
                "version": "1.21.3", "port": "25703", "memory_mb": "2048",
            }, headers={"HX-Request": "true"})
            sid = next(s["id"] for s in client.get("/api/servers").json() if s["name"] == "Stats")
            r = client.get(f"/servers/{sid}/stats")
            assert r.status_code == 200
            assert "12.5%" in r.text
            assert "512M" in r.text
            assert "2048M" in r.text
        finally:
            lc.Lifecycle.stats = original


def test_regenerate_pilot_rebuilds_bundle_with_current_env(app_with_docker, tmp_path):
    """Creating a server bakes the lifespan's public_host into the bundle.
    Changing the env + hitting POST /servers/{id}/pilot/regenerate should
    rebuild with the new host — no recreate needed."""
    with TestClient(app_with_docker) as client:
        st: AppState = app_with_docker.state.ndrchst
        pilots_root = tmp_path / "pilots"
        servers_root = tmp_path / "servers"
        st.lifecycle = Lifecycle(
            Docker(client=FakeClient()), st.conn,
            servers_root=servers_root,
            public_host="old.example",
            edge_url="",
        )
        import ndrchst.runtime.pilot as pilot_mod
        original_root = pilot_mod.PILOTS_ROOT_DEFAULT
        pilot_mod.PILOTS_ROOT_DEFAULT = pilots_root
        try:
            r = client.post("/servers", data={
                "name": "Rebuilder", "platform_id": "paper",
                "version": "1.21.3", "port": "25571", "memory_mb": "2048",
            }, headers={"HX-Request": "true"})
            assert r.status_code == 200, r.text
            sid = next(s["id"] for s in client.get("/api/servers").json()
                       if s["name"] == "Rebuilder")

            import json
            man = json.loads((pilots_root / sid / "manifest.json").read_text())
            assert man["host"] == "old.example"
            assert man["edge_url"] == ""

            # Flip the live env on the lifecycle (simulating an operator
            # editing systemd Environment= and restarting).
            st.lifecycle._public_host = "mc.ndrchst.com"
            st.lifecycle._edge_url = "https://play.ndrchst.com"

            r = client.post(f"/servers/{sid}/pilot/regenerate")
            assert r.status_code == 200
            assert "Rebuilt pilot bundle" in r.text

            man2 = json.loads((pilots_root / sid / "manifest.json").read_text())
            assert man2["host"] == "mc.ndrchst.com"
            assert man2["edge_url"] == "https://play.ndrchst.com"
        finally:
            pilot_mod.PILOTS_ROOT_DEFAULT = original_root


def test_regenerate_pilot_bedrock_rejected(app_with_docker, tmp_path):
    """Bedrock servers have no pilot bundle; the route must 400."""
    with TestClient(app_with_docker) as client:
        st: AppState = app_with_docker.state.ndrchst
        st.lifecycle = Lifecycle(
            Docker(client=FakeClient()), st.conn,
            servers_root=tmp_path / "servers", public_host="x",
        )
        r = client.post("/servers", data={
            "name": "Bdrk", "platform_id": "bedrock",
            "version": "latest", "port": "19501", "memory_mb": "1024",
        }, headers={"HX-Request": "true"})
        assert r.status_code == 200, r.text
        sid = next(s["id"] for s in client.get("/api/servers").json()
                   if s["name"] == "Bdrk")
        r = client.post(f"/servers/{sid}/pilot/regenerate")
        assert r.status_code == 400


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
