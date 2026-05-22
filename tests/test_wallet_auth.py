"""End-to-end tests for the Sign-In-With-Solana flow on the public surface.

Production ed25519 is verify-only, so this test carries a minimal RFC 8032
*signer* (reusing the curve primitives from domain.wallet) to forge a real
signature over the server's challenge — no third-party crypto library.
"""
from __future__ import annotations

import base64
import hashlib

import pytest
from fastapi.testclient import TestClient

from ndrchst.domain import wallet as W
from ndrchst.domain.wallet import _B, _scalarmult  # internal curve primitives
from ndrchst.public import create_public_app

_L = 2**252 + 27742317777372353535851937790883648493
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _encodepoint(p: tuple[int, int]) -> bytes:
    x, y = p
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\x00"))) + out


def _keypair(seed: bytes) -> tuple[str, bytes]:
    """Return (base58 pubkey, full 64-byte secret = seed||pub-bytes-unused)."""
    h = hashlib.sha512(seed).digest()
    hb = bytearray(h[:32])
    hb[0] &= 248
    hb[31] &= 127
    hb[31] |= 64
    a = int.from_bytes(hb, "little")
    pub = _encodepoint(_scalarmult(_B, a))
    return _b58encode(pub), seed


def _sign(seed: bytes, msg: bytes) -> bytes:
    h = hashlib.sha512(seed).digest()
    hb = bytearray(h[:32])
    hb[0] &= 248
    hb[31] &= 127
    hb[31] |= 64
    a = int.from_bytes(hb, "little")
    prefix = h[32:]
    pub = _encodepoint(_scalarmult(_B, a))
    r = int.from_bytes(hashlib.sha512(prefix + msg).digest(), "little") % _L
    rr = _encodepoint(_scalarmult(_B, r))
    k = int.from_bytes(hashlib.sha512(rr + pub + msg).digest(), "little") % _L
    s = (r + k * a) % _L
    return rr + s.to_bytes(32, "little")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("NDRCHST_COOKIE_SECURE", "0")  # so TestClient resends over http
    monkeypatch.setattr("ndrchst.runtime.solana.try_holdings_pct", lambda *a, **k: 0.7)
    app = create_public_app(db_path=tmp_path / "t.db")
    with TestClient(app) as c:
        yield c


def test_sign_in_with_solana_full_flow(client):
    pubkey, seed = _keypair(b"\x01" * 32)

    ch = client.post("/auth/challenge", json={"pubkey": pubkey})
    assert ch.status_code == 200
    msg = ch.json()["message"]
    assert pubkey in msg and "Nonce: " in msg

    sig = base64.b64encode(_sign(seed, msg.encode())).decode()
    v = client.post("/auth/verify", json={"pubkey": pubkey, "message": msg, "signature": sig})
    assert v.status_code == 200
    body = v.json()
    assert body["wallet"] == pubkey
    assert body["display"] == W.abbreviate(pubkey)
    assert body["tier"] == "silver"  # 0.07% -> silver
    assert client.cookies.get("ndrchst_session")

    me = client.get("/me")
    assert me.status_code == 200
    assert me.json()["wallet"] == pubkey

    client.post("/auth/logout")
    assert client.get("/me").status_code == 401


def test_verify_rejects_bad_signature(client):
    pubkey, seed = _keypair(b"\x02" * 32)
    msg = client.post("/auth/challenge", json={"pubkey": pubkey}).json()["message"]
    bad = bytearray(_sign(seed, msg.encode()))
    bad[0] ^= 0x01
    v = client.post("/auth/verify", json={
        "pubkey": pubkey, "message": msg, "signature": base64.b64encode(bytes(bad)).decode()})
    assert v.status_code == 401


def test_verify_rejects_replayed_nonce(client):
    pubkey, seed = _keypair(b"\x03" * 32)
    msg = client.post("/auth/challenge", json={"pubkey": pubkey}).json()["message"]
    sig = base64.b64encode(_sign(seed, msg.encode())).decode()
    first = client.post("/auth/verify", json={"pubkey": pubkey, "message": msg, "signature": sig})
    assert first.status_code == 200
    # same nonce again -> consumed
    second = client.post("/auth/verify", json={"pubkey": pubkey, "message": msg, "signature": sig})
    assert second.status_code == 401


def test_challenge_rejects_bad_pubkey(client):
    r = client.post("/auth/challenge", json={"pubkey": "not-a-real-key!!"})
    assert r.status_code == 400


def test_me_requires_session(client):
    assert client.get("/me").status_code == 401


def _approve(client, pubkey, seed, code):
    msg = client.post("/auth/challenge", json={"pubkey": pubkey}).json()["message"]
    sig = base64.b64encode(_sign(seed, msg.encode())).decode()
    return client.post("/client/auth/approve",
                       json={"code": code, "pubkey": pubkey, "message": msg, "signature": sig})


def test_client_pairing_full_flow(client):
    pubkey, seed = _keypair(b"\x10" * 32)
    start = client.post("/client/auth/start").json()
    assert start["user_code"] and start["pair_id"]
    assert start["verify_url"].endswith("/link?code=" + start["user_code"])

    # before approval -> pending
    poll = client.get("/client/auth/poll", params={"pair_id": start["pair_id"]})
    assert poll.status_code == 200 and poll.json()["status"] == "pending"

    # user connects wallet on /link and approves
    a = _approve(client, pubkey, seed, start["user_code"])
    assert a.status_code == 200 and a.json()["wallet"] == pubkey

    # client poll now sees the bound wallet + derived in-game name
    poll2 = client.get("/client/auth/poll", params={"pair_id": start["pair_id"]}).json()
    assert poll2["status"] == "approved"
    assert poll2["wallet"] == pubkey
    assert poll2["mc_name"] == W.derive_mc_name(pubkey)


def test_client_approve_rejects_unknown_code(client):
    pubkey, seed = _keypair(b"\x11" * 32)
    a = _approve(client, pubkey, seed, "ZZZZ-9999")
    assert a.status_code == 404


def test_client_poll_unknown_pair(client):
    assert client.get("/client/auth/poll", params={"pair_id": "nope"}).status_code == 404


def test_link_page_renders(client):
    r = client.get("/link", params={"code": "ABCD-2345"})
    assert r.status_code == 200
    assert "ABCD-2345" in r.text
    assert "Connect Wallet" in r.text


def test_approve_records_wallet_link(tmp_path, monkeypatch):
    from ndrchst.store import wallet_links as wl
    from ndrchst.store.db import connect

    monkeypatch.setenv("NDRCHST_COOKIE_SECURE", "0")
    monkeypatch.setattr("ndrchst.runtime.solana.try_holdings_pct", lambda *a, **k: 0.7)
    db = tmp_path / "t.db"
    app = create_public_app(db_path=db)
    with TestClient(app) as c:
        pubkey, seed = _keypair(b"\x20" * 32)
        start = c.post("/client/auth/start").json()
        assert _approve(c, pubkey, seed, start["user_code"]).status_code == 200

    link = wl.get(connect(db), pubkey)
    assert link is not None
    assert link.mc_name == W.derive_mc_name(pubkey)
    assert link.tier == "silver"  # 0.7% -> silver


def test_client_poll_carries_join_token_and_verifies(client):
    """The device-flow 'approved' response carries a join token; the mod would
    POST it to /join/verify, which returns the bound identity."""
    pubkey, seed = _keypair(b"\x30" * 32)
    start = client.post("/client/auth/start").json()
    assert _approve(client, pubkey, seed, start["user_code"]).status_code == 200
    poll = client.get("/client/auth/poll", params={"pair_id": start["pair_id"]}).json()
    assert poll["status"] == "approved"
    token = poll.get("join_token")
    assert token

    v = client.post("/join/verify", json={"token": token})
    assert v.status_code == 200
    body = v.json()
    assert body["ok"] is True
    assert body["wallet"] == pubkey
    assert body["mc_name"] == W.derive_mc_name(pubkey)
    assert body["tier"] == "silver"  # holdings_pct 0.7 -> silver


def test_join_verify_rejects_bad_token(client):
    assert client.post("/join/verify", json={"token": "garbage"}).status_code == 401


def test_join_verify_carries_imported_skin(client, monkeypatch, tmp_path):
    """The gate hands the mod the wallet's imported skin (Mojang hash + model)
    so it can apply it in-game; null when none is set."""
    import json as _json

    from ndrchst.domain import device_token as dt
    monkeypatch.setenv("NDRCHST_SKINS_DIR", str(tmp_path))
    wallet = "EUr2QnpmavMw51JiFYeTRnUywY7mPAtouzyY2P21pump"
    token = client.post("/device/exchange",
                        json={"device_token": dt.issue(wallet)}).json()["join_token"]
    # No skin sidecar yet → null.
    assert client.post("/join/verify", json={"token": token}).json()["skin"] is None
    # Simulate an import: write the sidecar the gate reads.
    safe = "".join(c for c in wallet if c.isalnum())
    (tmp_path / f"{safe}.json").write_text(
        _json.dumps({"texture": "a" * 64, "model": "slim"}))
    skin = client.post("/join/verify", json={"token": token}).json()["skin"]
    assert skin == {"texture": "a" * 64, "model": "slim"}


def test_device_exchange_returns_fresh_join_token(client):
    """The client trades its device token for a fresh join token at Play."""
    from ndrchst.domain import device_token as dt
    wallet = "EUr2QnpmavMw51JiFYeTRnUywY7mPAtouzyY2P21pump"
    r = client.post("/device/exchange", json={"device_token": dt.issue(wallet)})
    assert r.status_code == 200
    body = r.json()
    assert body["wallet"] == wallet
    assert body["tier"] == "silver"  # holdings_pct 0.7 -> silver
    assert body["join_token"]
    # …and that fresh join token verifies for the gate.
    v = client.post("/join/verify", json={"token": body["join_token"]})
    assert v.status_code == 200
    assert v.json()["mc_name"] == W.derive_mc_name(wallet)


def test_device_exchange_rejects_bad_token(client):
    assert client.post("/device/exchange", json={"device_token": "nope"}).status_code == 401


def test_tier_lookup_seeded_wallet(client):
    """`/tier` (the in-game readout source) returns the player's standing from
    the DB: current tier, holdings %, and the next threshold to climb."""
    from ndrchst.domain import device_token as dt
    wallet = "EUr2QnpmavMw51JiFYeTRnUywY7mPAtouzyY2P21pump"
    # Seed the link (device_exchange upserts holdings_pct=0.7 -> silver).
    client.post("/device/exchange", json={"device_token": dt.issue(wallet)})
    r = client.post("/tier", json={"wallet": wallet})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["tier"] == "silver"
    assert body["tier_name"] == "Silver"
    assert body["pct"] == 0.7
    # Next rung up the ladder is Gold at 1.0% — the mod renders the delta.
    assert body["next"] == {"key": "gold", "name": "Gold", "min_pct": 1.0}


def test_tier_lookup_unlinked_wallet_is_holder(client):
    """A wallet with no link (never signed in) floors to the base tier — the
    mod always has something to show, never a rankless/None state."""
    r = client.post("/tier", json={"wallet": "1nEvErLinKeDwaLLeT1111111111111111111111111"})
    assert r.status_code == 200
    body = r.json()
    assert body["tier"] == "holder"
    assert body["pct"] == 0.0
    assert body["next"] == {"key": "bronze", "name": "Bronze", "min_pct": 0.1}


def test_tier_lookup_includes_cached_price(client, monkeypatch):
    """The in-game readout carries the cached $NDRCHST ticker when present, and
    null when the box has nothing cached (decoration hides)."""
    from ndrchst.domain import device_token as dt
    from ndrchst.runtime import token_price
    wallet = "EUr2QnpmavMw51JiFYeTRnUywY7mPAtouzyY2P21pump"
    client.post("/device/exchange", json={"device_token": dt.issue(wallet)})
    # conftest stubs the fetch → nothing cached → price is null.
    assert client.post("/tier", json={"wallet": wallet}).json()["price"] is None
    # A cached value flows through (no RPC — pure decoration read).
    monkeypatch.setattr(token_price, "get", lambda: {
        "price_usd": 0.0012, "market_cap": 1_200_000, "symbol": "NDRCHST",
        "url": "u", "age_s": 5})
    body = client.post("/tier", json={"wallet": wallet}).json()
    assert body["price"] == {"usd": 0.0012, "market_cap": 1_200_000}


def test_ops_config_get_and_set(client):
    """The op surface lists the runtime knobs and sets one live (persisted),
    with the new value reflected back."""
    cfg = client.post("/ops/config").json()
    assert cfg["ok"] is True
    assert "daily_cooldown_s" in cfg["config"]

    r = client.post("/ops/config/set", json={"key": "daily_cooldown_s", "value": 3600})
    assert r.status_code == 200
    body = r.json()
    assert body["value"] == 3600
    assert body["config"]["daily_cooldown_s"] == 3600
    # negative clamps to the floor (0)
    assert client.post("/ops/config/set",
                       json={"key": "price_interval_s", "value": -1}).json()["value"] == 0


def test_ops_config_set_rejects_unknown_knob(client):
    assert client.post("/ops/config/set",
                       json={"key": "bogus", "value": 1}).status_code == 400


def test_price_endpoint_for_tab_menu(client, monkeypatch):
    """GET /price feeds the in-game tab ticker: null when nothing cached, the
    cached value otherwise (no RPC)."""
    from ndrchst.runtime import token_price
    assert client.get("/price").json() == {"ok": True, "price": None}
    monkeypatch.setattr(token_price, "get", lambda: {
        "price_usd": 0.0012, "market_cap": 1_200_000, "symbol": "NDRCHST",
        "url": "u", "age_s": 3})
    assert client.get("/price").json() == {
        "ok": True, "price": {"usd": 0.0012, "market_cap": 1_200_000}}


def test_next_tier_ladder():
    """next_tier returns the lowest rung above the current holdings; None at top."""
    assert W.next_tier(0.0).key == "bronze"
    assert W.next_tier(0.42).key == "silver"   # 0.42% is bronze, next is silver
    assert W.next_tier(0.7).key == "gold"      # 0.7% is silver, next is gold
    assert W.next_tier(1.0).key == "diamond"   # exactly gold -> diamond is next
    assert W.next_tier(5.0) is None            # whale is the cap


def test_me_client_requires_session(client):
    assert client.get("/me/client/anyserver").status_code == 401


# --- Paper / cross-play identity gate (online-mode path) ---------------------

def _gate_approve(client, pubkey, seed, code):
    """Approve an in-game /link code on the web side (wallet-signed)."""
    msg = client.post("/auth/challenge", json={"pubkey": pubkey}).json()["message"]
    sig = base64.b64encode(_sign(seed, msg.encode())).decode()
    return client.post("/gate/link/approve",
                       json={"code": code, "pubkey": pubkey, "message": msg, "signature": sig})


def test_gate_identity_unlinked_returns_ok_false(client):
    """An authenticated UUID with no wallet link is a clean deny, not an error —
    the plugin uses it to kick with a 'link your wallet' prompt."""
    r = client.post("/gate/identity", json={"uuid": "00000000-0000-0000-0000-000000000001"})
    assert r.status_code == 200
    assert r.json() == {"ok": False, "reason": "unlinked"}


def test_identity_link_flow_then_gate(client):
    """In-game /link → wallet approve → gate now resolves the identity to a tier."""
    pubkey, seed = _keypair(b"\x20" * 32)
    uuid = "11111111-2222-3333-4444-555555555555"

    start = client.post("/gate/link/start", json={
        "uuid": uuid, "xuid": "2535400000000001", "username": "Steve"}).json()
    assert start["user_code"] and start["pair_id"]
    assert start["verify_url"].endswith(f"code={start['user_code']}&m=g")

    # still pending before approval
    pending = client.get("/gate/link/poll", params={"pair_id": start["pair_id"]})
    assert pending.json()["status"] == "pending"

    approved = _gate_approve(client, pubkey, seed, start["user_code"])
    assert approved.status_code == 200
    assert approved.json()["ok"] is True
    assert approved.json()["tier"] == "silver"  # holdings stub 0.7 -> silver

    # plugin poll now sees the binding (so it can re-gate live, no rejoin)
    polled = client.get("/gate/link/poll", params={"pair_id": start["pair_id"]}).json()
    assert polled["status"] == "approved" and polled["wallet"] == pubkey
    assert polled["tier"] == "silver"

    # the gate now resolves the UUID to the wallet + tier
    g = client.post("/gate/identity", json={"uuid": uuid}).json()
    assert g["ok"] is True and g["wallet"] == pubkey and g["tier"] == "silver"

    # and by Bedrock xuid alone (synthetic uuid may differ across sessions)
    gx = client.post("/gate/identity", json={
        "uuid": "ffffffff-0000-0000-0000-000000000000", "xuid": "2535400000000001"}).json()
    assert gx["ok"] is True and gx["wallet"] == pubkey


def test_gate_link_approve_rejects_unknown_code(client):
    pubkey, seed = _keypair(b"\x21" * 32)
    r = _gate_approve(client, pubkey, seed, "ZZZZ-ZZZZ")
    assert r.status_code == 404


def test_gate_endpoints_are_bridge_gated(client, monkeypatch):
    """The plugin-facing gate endpoints reject non-internal callers (like
    /join/verify). TestClient counts as internal; force a public source IP."""
    monkeypatch.setattr("ndrchst.public._is_internal_caller", lambda *_a, **_k: False)
    assert client.post("/gate/identity", json={"uuid": "x"}).status_code == 403
    assert client.post("/gate/link/start", json={"uuid": "x"}).status_code == 403
    assert client.get("/gate/link/poll", params={"pair_id": "x"}).status_code == 403
