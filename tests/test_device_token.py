"""Tests for the long-lived device token (the pilot's durable credential)."""
from __future__ import annotations

from ndrchst.domain import device_token, join_token


def test_issue_verify_round_trip():
    tok = device_token.issue("WALLET1")
    assert device_token.verify(tok) == "WALLET1"


def test_verify_rejects_tamper():
    tok = device_token.issue("WALLET1")
    p, s = tok.split(".", 1)
    bad = p + "." + ("A" if s[0] != "A" else "B") + s[1:]
    assert device_token.verify(bad) is None


def test_verify_rejects_expired():
    assert device_token.verify(device_token.issue("W", ttl=-1)) is None


def test_verify_rejects_garbage():
    for bad in (None, "", "nodot", "a.b"):
        assert device_token.verify(bad) is None


def test_domain_separation_from_join_token(monkeypatch):
    """A device token must not validate as a join token, or vice versa."""
    monkeypatch.setenv("NDRCHST_SESSION_SECRET", "shared")
    dev = device_token.issue("WALLET1")
    assert join_token.verify(dev) is None
    jt = join_token.issue("WALLET1", "name", "gold")
    assert device_token.verify(jt) is None
