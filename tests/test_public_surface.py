"""Public surface tests: client downloads, server listing, no admin leaks."""
from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

from fastapi.testclient import TestClient

from ndrchst.domain.models import Family, Server, ServerStatus
from ndrchst.public import create_public_app
from ndrchst.runtime import client
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
        assert body[0]["client_url"] == "/client/srvjava01/client.zip"


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
        # Play is gated behind wallet sign-in (auth-first): a client-dl button
        # carrying the server id, which hands off to the installed app.
        assert 'class="btn client-dl" data-sid="srvjava01"' in r.text
        assert "Sign in to play" in r.text          # signed-out label
        assert 'data-label-in="Play"' in r.text     # becomes "Play" once signed in
        assert 'id="client-download"' in r.text      # primary exe download CTA
        assert "/me/handoff" in r.text               # deep-link handoff wiring
        assert "bedrock 19150/udp" in r.text


def test_session_cookie_is_host_scoped(tmp_path: Path):
    # Single host → the session cookie carries NO Domain attribute (host-scoped),
    # which is the whole point of the collapse: it can't strand on a sibling host.
    # auth/logout sets/clears the same cookie with the same attributes as verify.
    db = tmp_path / "t.db"
    app = create_public_app(db_path=db)
    with TestClient(app) as c:
        r = c.post("/auth/logout")
        set_cookie = r.headers.get("set-cookie", "")
        assert "ndrchst_session=" in set_cookie
        assert "domain=" not in set_cookie.lower()


def test_nav_links_are_same_host_relative(monkeypatch):
    # The surface is one host now, so Home and Play are same-host relative paths
    # regardless of any edge/play env — no cross-host absolute URLs in the nav.
    from ndrchst.web.public_pages import _home_url, _play_url, render_play
    monkeypatch.setenv("NDRCHST_EDGE_URL", "https://play.ndrchst.com")
    assert _home_url() == "/"
    assert _play_url() == "/play"
    html = render_play([])
    assert 'href="/"' in html
    assert 'href="/play"' in html
    assert "ndrchst.com" not in html  # no cross-host links leak into the page


def test_ranks_renders_ladder_and_per_tier_counts(tmp_path: Path):
    from ndrchst.store import wallet_links as wl
    db = tmp_path / "t.db"
    conn = connect(db)
    wl.upsert(conn, "EUr2QnpmavMw51JiFYeTRnUywY7mPAtouzyY2P21pump",
              "EUr2Qn_pump", "gold", 1.25)
    wl.upsert(conn, "SoMeOtherWalletAddrxxxxxxxxxxxxxxxxxxxxxx",
              "SoMeOt_xxxx", None, 0.0)  # no holdings → not counted
    conn.close()

    app = create_public_app(db_path=db)
    with TestClient(app) as c:
        r = c.get("/ranks")
        assert r.status_code == 200
        # Tier ladder is always present, one nested card per tier.
        for name in ("Holder", "Bronze", "Silver", "Gold", "Diamond", "Whale"):
            assert name in r.text
        assert 'data-tier="gold"' in r.text
        # Per-tier holder COUNT replaces the leaderboard roster: one in gold,
        # the empty tiers say so, and no individual wallet rows are exposed.
        assert "1 holder" in r.text
        assert "no holders yet" in r.text
        assert "EUr2…pump" not in r.text and "SoMeOt_xxxx" not in r.text
        assert 'href="/ranks"' in r.text  # nav link present


def test_me_client_bakes_device_token_into_bundle(tmp_path: Path, monkeypatch):
    import io
    import zipfile

    from ndrchst.domain import auth_session, device_token
    monkeypatch.setenv("NDRCHST_COOKIE_SECURE", "0")
    clients_root = tmp_path / "clients"
    monkeypatch.setattr(client, "CLIENTS_ROOT_DEFAULT", clients_root)
    db = tmp_path / "t.db"
    s = _seed_server(db)
    client.build_bundle(s, public_host="mc.x", clients_root=clients_root)

    app = create_public_app(db_path=db)
    with TestClient(app) as c:
        # no session -> 401
        assert c.get(f"/me/client/{s.id}").status_code == 401
        # with a wallet session -> personalized bundle carrying a device token
        c.cookies.set("ndrchst_session", auth_session.sign_session("WALLETxyz"))
        r = c.get(f"/me/client/{s.id}")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        assert "ndrchst-device.token" in zf.namelist()
        assert "ndrchst_client/config.py" in zf.namelist()  # the real bundle, intact
        baked = zf.read("ndrchst-device.token").decode()
        assert device_token.verify(baked) == "WALLETxyz"


def test_handoff_mint_and_redeem(tmp_path: Path):
    """The play page mints a one-time handoff code for a signed-in wallet; the
    installed client redeems it (no session) for a device token + identity. The
    URL only ever carries the single-use code, never a credential."""
    from ndrchst.domain import auth_session, client_handoff, device_token
    app = create_public_app(db_path=tmp_path / "t.db")
    with TestClient(app) as c:
        # minting requires a wallet session
        assert c.post("/me/handoff").status_code == 401
        c.cookies.set("ndrchst_session", auth_session.sign_session("WALLETxyz"))
        code = c.post("/me/handoff").json()["code"]
        assert code and isinstance(code, str)
        # redeem as the client would — WITHOUT a session — for token + identity
        c.cookies.delete("ndrchst_session")
        body = c.post("/client/auth/handoff", json={"code": code})
        assert body.status_code == 200
        j = body.json()
        assert j["wallet"] == "WALLETxyz"
        assert j.get("join_token")                            # the mod credential
        assert device_token.verify(j["device_token"]) == "WALLETxyz"
        # single-use: the same code can't be redeemed again
        assert c.post("/client/auth/handoff", json={"code": code}).status_code == 404
        # an unknown code is rejected
        assert c.post("/client/auth/handoff", json={"code": "nope"}).status_code == 404
        assert client_handoff.redeem(code) is None            # nothing lingers in-process


def test_internal_caller_guard():
    """Public-tunnel traffic (loopback via cloudflared) is rejected; the mod's
    Docker-bridge traffic (and TestClient) is allowed."""
    from types import SimpleNamespace

    from ndrchst.public import _is_internal_caller

    def req(host):
        return SimpleNamespace(client=SimpleNamespace(host=host))

    assert _is_internal_caller(req("172.17.0.5")) is True   # docker bridge
    assert _is_internal_caller(req("10.0.0.4")) is True      # private
    assert _is_internal_caller(req("testclient")) is True    # TestClient
    assert _is_internal_caller(req("127.0.0.1")) is False    # tunnel/loopback
    assert _is_internal_caller(req("8.8.8.8")) is False       # public internet


def test_join_verify_uses_snapshot_floor_tier(tmp_path: Path):
    from ndrchst.domain import join_token
    from ndrchst.store import wallet_links as wl
    db = tmp_path / "t.db"
    conn = connect(db)
    wl.upsert(conn, "GOLDWALLET", "Gold_name", "gold", 1.2)
    conn.close()
    app = create_public_app(db_path=db)
    with TestClient(app) as c:
        tok = join_token.issue("GOLDWALLET", "Gold_name", "gold")
        r = c.post("/join/verify", json={"token": tok})
        assert r.status_code == 200
        assert r.json()["tier"] == "gold"     # from the stored link, not a live RPC
        # An unknown (but validly-signed) wallet floors to the base tier.
        tok2 = join_token.issue("NOLINK", "NoLink_x", None)
        assert c.post("/join/verify", json={"token": tok2}).json()["tier"] == "holder"
        # A garbage token is still rejected.
        assert c.post("/join/verify", json={"token": "nope"}).status_code == 401


def test_daily_claim_cooldown_and_snapshot_tier(tmp_path: Path):
    from ndrchst.store import wallet_links as wl
    db = tmp_path / "t.db"
    conn = connect(db)
    wl.upsert(conn, "WHALEW", "Whale_w", "whale", 6.0)
    wl.set_snapshot(conn, "WHALEW", "whale", 6.0)  # the hourly snapshot the daily reads
    conn.close()
    app = create_public_app(db_path=db)
    with TestClient(app) as c:
        r = c.post("/daily/claim", json={"wallet": "WHALEW"})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["tier"] == "whale"
        # Second claim is on cooldown.
        r2 = c.post("/daily/claim", json={"wallet": "WHALEW"})
        assert r2.json()["ok"] is False
        assert r2.json()["seconds_left"] > 0
        # Op reset clears it.
        assert c.post("/daily/reset", json={"wallet": "WHALEW"}).status_code == 200
        assert c.post("/daily/claim", json={"wallet": "WHALEW"}).json()["ok"] is True


def test_daily_claim_floors_to_base_without_snapshot(tmp_path: Path):
    """A wallet that signed in but hasn't been snapshotted yet still earns the
    base daily — just showing up is rewarded."""
    db = tmp_path / "t.db"
    app = create_public_app(db_path=db)
    with TestClient(app) as c:
        r = c.post("/daily/claim", json={"wallet": "FRESHWALLET"})
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["tier"] == "holder"


def test_download_client_404_when_no_bundle(tmp_path: Path):
    db = tmp_path / "t.db"
    _seed_server(db)
    app = create_public_app(db_path=db)
    with TestClient(app) as c:
        r = c.get("/client/srvjava01/client.zip")
        assert r.status_code == 404


def test_download_client_serves_zip(tmp_path: Path, monkeypatch):
    db = tmp_path / "t.db"
    s = _seed_server(db)
    # Build a bundle into a custom root and point the public surface at it
    clients_root = tmp_path / "clients"
    client.build_bundle(s, public_host="100.89.8.49", clients_root=clients_root)
    monkeypatch.setattr(client, "CLIENTS_ROOT_DEFAULT", clients_root)

    app = create_public_app(db_path=db)
    with TestClient(app) as c:
        r = c.get("/client/srvjava01/client.zip")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        assert len(r.content) > 1000
        # Cache opt-out: bundle can be regenerated server-side at any time,
        # we don't want a CDN to serve a stale copy after that happens.
        assert "no-store" in r.headers.get("cache-control", "")


def test_download_client_404_for_unknown_server(tmp_path: Path):
    db = tmp_path / "t.db"
    app = create_public_app(db_path=db)
    with TestClient(app) as c:
        r = c.get("/client/nope/client.zip")
        assert r.status_code == 404


def test_config_json_endpoint(tmp_path: Path, monkeypatch):
    db = tmp_path / "t.db"
    s = _seed_server(db)
    clients_root = tmp_path / "clients"
    client.build_bundle(s, public_host="x.example", clients_root=clients_root)
    monkeypatch.setattr(client, "CLIENTS_ROOT_DEFAULT", clients_root)

    app = create_public_app(db_path=db)
    with TestClient(app) as c:
        r = c.get(f"/client/{s.id}/config.json")
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


# --- new public-surface behavior ---------------------------------------------
def _png(w: int, h: int) -> bytes:
    """Smallest valid PNG of the given dimensions (for skin-upload tests)."""
    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    idat = zlib.compress((b"\x00" + b"\x00\x00\x00\x00" * w) * h)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _tiers():
    from ndrchst.domain.wallet import DEFAULT_TIERS
    return [{"key": t.key, "name": t.name, "min_pct": t.min_pct} for t in DEFAULT_TIERS]


def test_landing_drops_tier_teaser_but_keeps_chips():
    from ndrchst.web.public_pages import render_landing
    html = render_landing()
    assert "Six tiers, read from the chain" not in html  # moved to /ranks
    assert "See the full ladder" not in html
    assert 'class="floats"' in html and 'class="float ' not in html  # JS-populated, not hardcoded
    assert "/game/items/" in html  # deco chips still present, sampled from drops


def test_ranks_has_bands_and_transparency_table():
    from ndrchst.web.public_pages import render_ranks
    html = render_ranks([], _tiers())
    assert "≥ 5% of supply" in html          # whale: open-ended top band
    assert "0.1% – 0.5% of supply" in html   # bronze: exact band  # noqa: RUF001
    assert "any holdings · base tier" in html  # holder floor
    # Transparency presentation: per-roll breakdown with amounts + exact odds.
    assert "<code>/daily</code>" in html and "real loot tables" in html
    assert "Roll 1" in html and 'class="rpct mono"' in html  # odds column
    # Additive demonstration is shown as a compact chip, not prose copy.
    assert "+ every lower tier, daily" in html
    assert "no holders yet" in html  # per-tier count replaces the leaderboard


def test_ranks_drop_odds_sum_to_100_per_roll():
    """Odds are derived from the loot-table weights, so each roll's entries
    must total 100% — proving the page reads the real source, not a fiction."""
    from ndrchst.web import public_pages as P
    P._tier_loot.cache_clear()
    loot = P._tier_loot()
    P._tier_loot.cache_clear()
    assert loot, "expected loot tables to be present in the repo"
    for tier, rolls in loot.items():
        for i, entries in enumerate(rolls):
            assert sum(e["pct"] for e in entries) == 100, (tier, i, entries)


def test_drops_come_from_loot_tables(monkeypatch, tmp_path: Path):
    """The drops are read from the datapack loot tables — not a hardcoded dict —
    so editing a loot table changes the page."""
    from ndrchst.web import public_pages as P
    lt = tmp_path / "loot"
    lt.mkdir()
    (lt / "whale.json").write_text(json.dumps({
        "pools": [{"entries": [
            {"type": "minecraft:item", "name": "minecraft:diamond",
             "functions": [{"function": "minecraft:set_count",
                            "count": {"min": 9, "max": 9}}]},
        ]}]}))
    monkeypatch.setenv("NDRCHST_LOOT_TABLES_DIR", str(lt))
    P._tier_drops.cache_clear()
    drops = P._tier_drops()
    P._tier_drops.cache_clear()  # don't leak the override into other tests
    assert drops["whale"] == [{"icon": "diamond", "name": "Diamond", "min": 9, "max": 9}]


def test_link_page_uses_shared_wallet_plumbing():
    from ndrchst.web.public_pages import render_link
    html = render_link(code="ABC123")
    assert "ABC123" in html
    assert "window.ndrchstWallet.requestSignature" in html  # not a bespoke copy
    assert "/client/auth/approve" in html
    assert 'class="floats"' in html  # shares the full shell now


def test_maintenance_page(tmp_path: Path):
    db = tmp_path / "t.db"
    app = create_public_app(db_path=db)
    with TestClient(app) as c:
        r = c.get("/maintenance")
        assert r.status_code == 503
        assert "Be right back" in r.text
        assert "ndrchstWallet" in r.text  # unified wallet control present


def test_skin_upload_validate_store_serve_delete(tmp_path: Path, monkeypatch):
    from ndrchst.domain import auth_session
    monkeypatch.setenv("NDRCHST_COOKIE_SECURE", "0")
    monkeypatch.setenv("NDRCHST_SKINS_DIR", str(tmp_path / "skins"))
    app = create_public_app(db_path=tmp_path / "t.db")
    with TestClient(app) as c:
        # signed-out → 401
        assert c.post("/me/skin", content=_png(64, 64)).status_code == 401
        c.cookies.set("ndrchst_session", auth_session.sign_session("WALLETxyz"))
        # wrong dimensions / non-PNG → 400
        assert c.post("/me/skin", content=_png(48, 48)).status_code == 400
        assert c.post("/me/skin", content=b"not a png").status_code == 400
        # valid 64x64 → stored, /me + GET reflect it
        r = c.post("/me/skin", content=_png(64, 64))
        assert r.status_code == 200 and r.json()["skin_url"] == "/skins/WALLETxyz.png"
        assert c.get("/me").json()["skin_url"] == "/skins/WALLETxyz.png"
        g = c.get("/skins/WALLETxyz.png")
        assert g.status_code == 200 and g.headers["content-type"] == "image/png"
        # delete → gone
        assert c.delete("/me/skin").status_code == 200
        assert c.get("/skins/WALLETxyz.png").status_code == 404
        assert c.get("/me").json()["skin_url"] is None


def test_skin_too_large_rejected(tmp_path: Path, monkeypatch):
    from ndrchst.domain import auth_session
    monkeypatch.setenv("NDRCHST_COOKIE_SECURE", "0")
    monkeypatch.setenv("NDRCHST_SKINS_DIR", str(tmp_path / "skins"))
    app = create_public_app(db_path=tmp_path / "t.db")
    with TestClient(app) as c:
        c.cookies.set("ndrchst_session", auth_session.sign_session("WALLETxyz"))
        assert c.post("/me/skin", content=b"x" * (256 * 1024 + 1)).status_code == 413


def test_skin_search_preview_and_import(tmp_path: Path, monkeypatch):
    """Username search → preview proxy → one-click import, all gated to a
    signed-in wallet. Mojang is mocked (mojang module is unit-tested separately)."""
    from ndrchst.domain import auth_session
    from ndrchst.runtime import mojang
    monkeypatch.setenv("NDRCHST_COOKIE_SECURE", "0")
    monkeypatch.setenv("NDRCHST_SKINS_DIR", str(tmp_path / "skins"))
    tex = "a" * 64
    monkeypatch.setattr(mojang, "lookup_skin", lambda q, **k: (
        {"name": "Notch", "uuid": "u", "texture": tex, "model": "classic"}
        if q == "Notch" else None))
    monkeypatch.setattr(mojang, "fetch_texture",
                        lambda h, **k: _png(64, 64) if h == tex else None)
    app = create_public_app(db_path=tmp_path / "t.db")
    with TestClient(app) as c:
        # search is gated to signed-in (no open Mojang proxy)
        assert c.get("/me/skin/search?q=Notch").status_code == 401
        c.cookies.set("ndrchst_session", auth_session.sign_session("WALLETxyz"))
        res = c.get("/me/skin/search?q=Notch").json()["results"]
        assert res and res[0]["texture"] == tex
        assert res[0]["preview_url"] == f"/me/skin/preview/{tex}"
        assert c.get("/me/skin/search?q=Ghost").json()["results"] == []
        # preview proxies the texture bytes as image/png
        p = c.get(f"/me/skin/preview/{tex}")
        assert p.status_code == 200 and p.headers["content-type"] == "image/png"
        # import stores under the wallet, surfaced via /me + /skins
        im = c.post("/me/skin/import", json={"texture": tex})
        assert im.status_code == 200 and im.json()["skin_url"] == "/skins/WALLETxyz.png"
        assert c.get("/me").json()["skin_url"] == "/skins/WALLETxyz.png"
        # import requires a session
        c.cookies.delete("ndrchst_session")
        assert c.post("/me/skin/import", json={"texture": tex}).status_code == 401


def test_ranks_shows_random_treasure_pull():
    """Each daily crate also pulls from a vanilla treasure table (built-in random
    loot) — surfaced on /ranks, read from the loot table's minecraft: refs."""
    from ndrchst.web import public_pages as P
    P._tier_loot.cache_clear()
    html = P.render_ranks([], _tiers())
    assert "treasure pull" in html.lower()    # explainer mentions the bonus
    assert "treasure: End City" in html        # diamond's vanilla treasure, as a chip
    assert P._tier_treasure("diamond") == ["End City"]


def test_ranks_themed_as_daily_crate():
    """Daily IS the crate now — /ranks frames the additive daily as the crate,
    with no separate keys/crate mechanic surfaced."""
    from ndrchst.web.public_pages import render_ranks
    html = render_ranks([], _tiers())
    assert "daily crate" in html.lower()
    assert "Crates &amp; keys" not in html       # separate crate section gone
    assert 'data-crate=' not in html             # no key-balance slots
    assert "/crate common|rare|legendary" not in html


def test_pages_cache_identity_to_avoid_flicker():
    """A signed-in player must not flash the signed-out layout on navigation:
    the head bootstrap paints the login class from cache before first render,
    and render() persists identity to localStorage on <html> (not <body>)."""
    from ndrchst.web.public_pages import render_landing, render_play
    home = render_landing()
    assert "localStorage.getItem('ndrchst_me')" in home  # pre-render class paint
    assert "localStorage.setItem('ndrchst_me'" in home    # render persists identity
    assert "document.documentElement.classList" in home   # class on <html>
    assert "html.signed-in" in home                       # CSS keys off <html>
    play = render_play([])
    assert "skin-q" in play and "/me/skin/search" in play  # username search wired
    assert "minecraftskins.com" in play                    # Skindex jump-link
