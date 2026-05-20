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
    monkeypatch.setattr("ndrchst.runtime.solana.holdings_pct", lambda *a, **k: 0.7)
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
    return client.post("/pilot/auth/approve",
                       json={"code": code, "pubkey": pubkey, "message": msg, "signature": sig})


def test_pilot_pairing_full_flow(client):
    pubkey, seed = _keypair(b"\x10" * 32)
    start = client.post("/pilot/auth/start").json()
    assert start["user_code"] and start["pair_id"]
    assert start["verify_url"].endswith("/link?code=" + start["user_code"])

    # before approval -> pending
    poll = client.get("/pilot/auth/poll", params={"pair_id": start["pair_id"]})
    assert poll.status_code == 200 and poll.json()["status"] == "pending"

    # user connects wallet on /link and approves
    a = _approve(client, pubkey, seed, start["user_code"])
    assert a.status_code == 200 and a.json()["wallet"] == pubkey

    # pilot poll now sees the bound wallet + derived in-game name
    poll2 = client.get("/pilot/auth/poll", params={"pair_id": start["pair_id"]}).json()
    assert poll2["status"] == "approved"
    assert poll2["wallet"] == pubkey
    assert poll2["mc_name"] == W.derive_mc_name(pubkey)


def test_pilot_approve_rejects_unknown_code(client):
    pubkey, seed = _keypair(b"\x11" * 32)
    a = _approve(client, pubkey, seed, "ZZZZ-9999")
    assert a.status_code == 404


def test_pilot_poll_unknown_pair(client):
    assert client.get("/pilot/auth/poll", params={"pair_id": "nope"}).status_code == 404


def test_link_page_renders(client):
    r = client.get("/link", params={"code": "ABCD-2345"})
    assert r.status_code == 200
    assert "ABCD-2345" in r.text
    assert "Connect Wallet" in r.text
