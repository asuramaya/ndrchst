"""Public surface tests: pilot downloads, server listing, no admin leaks."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ndrchst.domain.models import Family, Server, ServerStatus
from ndrchst.public import create_public_app
from ndrchst.runtime import pilot
from ndrchst.store import servers as srv_store
from ndrchst.store.db import connect


def _seed_server(db_path: Path, *, family=Family.JAVA, cross_play=False) -> Server:
    conn = connect(db_path)
    s = Server(
        id="srvjava01",
        name="Public Test",
        platform_id="paper",
        family=family,
        version="1.21.11",
        port=25574,
        memory_mb=2048,
        status=ServerStatus.RUNNING,
        container_id="cid",
        cross_play=cross_play,
        bedrock_bridge_port=19150 if cross_play else None,
    )
    srv_store.insert(conn, s)
    conn.close()
    return s


def test_healthz_marks_public(tmp_path: Path):
    db = tmp_path / "t.db"
    app = create_public_app(db_path=db)
    with TestClient(app) as c:
        r = c.get("/healthz")
        assert r.status_code == 200
        assert r.json()["surface"] == "public"


def test_list_servers_only_includes_java(tmp_path: Path, monkeypatch):
    db = tmp_path / "t.db"
    s = _seed_server(db)
    # also seed a bedrock server — should be hidden from the public surface
    conn = connect(db)
    bedrock = Server(
        id="bdrk001", name="HiddenBedrock", platform_id="bedrock",
        family=Family.BEDROCK, version="latest", port=19132, memory_mb=1024,
        status=ServerStatus.RUNNING, container_id="cid2",
    )
    srv_store.insert(conn, bedrock)
    conn.close()

    app = create_public_app(db_path=db)
    with TestClient(app) as c:
        r = c.get("/servers")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["id"] == s.id
        assert body[0]["mc_version"] == "1.21.11"
        assert body[0]["pilot_url"] == "/pilot/srvjava01/pilot.zip"


def test_landing_renders_marketing(tmp_path: Path):
    db = tmp_path / "t.db"
    app = create_public_app(db_path=db)
    with TestClient(app) as c:
        r = c.get("/")
        assert r.status_code == 200
        assert "Minecraft" in r.text
        assert "Solana" in r.text
        assert "NDRCHST" in r.text
        assert 'href="/play"' in r.text


def test_play_renders_html_with_download_link(tmp_path: Path):
    db = tmp_path / "t.db"
    _seed_server(db, cross_play=True)
    app = create_public_app(db_path=db)
    with TestClient(app) as c:
        r = c.get("/play")
        assert r.status_code == 200
        assert "Public Test" in r.text
        # Download is now gated behind wallet sign-in (auth-first): a pilot-dl
        # button carrying the server id, not a bare download link.
        assert 'class="btn pilot-dl" data-sid="srvjava01"' in r.text
        assert "Sign in to download" in r.text
        assert "bedrock 19150/udp" in r.text


def test_ranks_renders_ladder_and_holders(tmp_path: Path):
    from ndrchst.store import wallet_links as wl
    db = tmp_path / "t.db"
    conn = connect(db)
    wl.upsert(conn, "EUr2QnpmavMw51JiFYeTRnUywY7mPAtouzyY2P21pump",
              "EUr2Qn_pump", "gold", 1.25)
    wl.upsert(conn, "SoMeOtherWalletAddrxxxxxxxxxxxxxxxxxxxxxx",
              "SoMeOt_xxxx", None, 0.0)  # no holdings → excluded from board
    conn.close()

    app = create_public_app(db_path=db)
    with TestClient(app) as c:
        r = c.get("/ranks")
        assert r.status_code == 200
        # Tier ladder is always present.
        for name in ("Holder", "Bronze", "Silver", "Gold", "Diamond", "Whale"):
            assert name in r.text
        # The gold holder shows up; the zero-holdings wallet does not.
        assert "EUr2…pump" in r.text
        assert "1.2500%" in r.text
        assert "SoMeOt_xxxx" not in r.text
        assert 'href="/ranks"' in r.text  # nav link present


def test_me_pilot_bakes_device_token_into_bundle(tmp_path: Path, monkeypatch):
    import io
    import zipfile

    from ndrchst.domain import auth_session, device_token
    monkeypatch.setenv("NDRCHST_COOKIE_SECURE", "0")
    pilots_root = tmp_path / "pilots"
    monkeypatch.setattr(pilot, "PILOTS_ROOT_DEFAULT", pilots_root)
    db = tmp_path / "t.db"
    s = _seed_server(db)
    pilot.build_bundle(s, public_host="mc.x", pilots_root=pilots_root)

    app = create_public_app(db_path=db)
    with TestClient(app) as c:
        # no session -> 401
        assert c.get(f"/me/pilot/{s.id}").status_code == 401
        # with a wallet session -> personalized bundle carrying a device token
        c.cookies.set("ndrchst_session", auth_session.sign_session("WALLETxyz"))
        r = c.get(f"/me/pilot/{s.id}")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        assert "ndrchst-device.token" in zf.namelist()
        assert "ndrchst_pilot/config.py" in zf.namelist()  # the real bundle, intact
        baked = zf.read("ndrchst-device.token").decode()
        assert device_token.verify(baked) == "WALLETxyz"


def test_download_pilot_404_when_no_bundle(tmp_path: Path):
    db = tmp_path / "t.db"
    _seed_server(db)
    app = create_public_app(db_path=db)
    with TestClient(app) as c:
        r = c.get("/pilot/srvjava01/pilot.zip")
        assert r.status_code == 404


def test_download_pilot_serves_zip(tmp_path: Path, monkeypatch):
    db = tmp_path / "t.db"
    s = _seed_server(db)
    # Build a bundle into a custom root and point the public surface at it
    pilots_root = tmp_path / "pilots"
    pilot.build_bundle(s, public_host="100.89.8.49", pilots_root=pilots_root)
    monkeypatch.setattr(pilot, "PILOTS_ROOT_DEFAULT", pilots_root)

    app = create_public_app(db_path=db)
    with TestClient(app) as c:
        r = c.get("/pilot/srvjava01/pilot.zip")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        assert len(r.content) > 1000
        # Cache opt-out: bundle can be regenerated server-side at any time,
        # we don't want a CDN to serve a stale copy after that happens.
        assert "no-store" in r.headers.get("cache-control", "")


def test_download_pilot_404_for_unknown_server(tmp_path: Path):
    db = tmp_path / "t.db"
    app = create_public_app(db_path=db)
    with TestClient(app) as c:
        r = c.get("/pilot/nope/pilot.zip")
        assert r.status_code == 404


def test_config_json_endpoint(tmp_path: Path, monkeypatch):
    db = tmp_path / "t.db"
    s = _seed_server(db)
    pilots_root = tmp_path / "pilots"
    pilot.build_bundle(s, public_host="x.example", pilots_root=pilots_root)
    monkeypatch.setattr(pilot, "PILOTS_ROOT_DEFAULT", pilots_root)

    app = create_public_app(db_path=db)
    with TestClient(app) as c:
        r = c.get(f"/pilot/{s.id}/config.json")
        assert r.status_code == 200
        cfg = r.json()
        assert cfg["mc_version"] == "1.21.11"
        assert cfg["server_host"] == "x.example"


def test_no_admin_routes_on_public_app(tmp_path: Path):
    """Public app must NOT expose any of the admin routes."""
    db = tmp_path / "t.db"
    app = create_public_app(db_path=db)
    with TestClient(app) as c:
        for path in ("/api/servers", "/api/platforms", "/system", "/settings", "/assets", "/servers/new"):
            r = c.get(path)
            assert r.status_code == 404, f"public app leaked admin route {path}"
        # POST /servers exists as GET only; reject the verb. Same for DELETE.
        r = c.post("/servers", data={})
        assert r.status_code in (404, 405)
        r = c.delete("/servers/abc")
        assert r.status_code in (404, 405)


def test_empty_state_renders_friendly(tmp_path: Path):
    db = tmp_path / "t.db"
    app = create_public_app(db_path=db)
    with TestClient(app) as c:
        r = c.get("/play")
        assert r.status_code == 200
        assert "No servers are online" in r.text
