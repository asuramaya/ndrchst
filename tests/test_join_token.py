"""Tests for the HMAC join-token (the modded-server gate credential)."""
from __future__ import annotations

import time

from ndrchst.domain import auth_session, join_token


def test_issue_verify_round_trip():
    tok = join_token.issue("WALLET1", "EUr2Qn_pump", "gold")
    claims = join_token.verify(tok)
    assert claims == {"wallet": "WALLET1", "mc_name": "EUr2Qn_pump", "tier": "gold"}


def test_verify_rejects_tamper():
    tok = join_token.issue("WALLET1", "EUr2Qn_pump", "gold")
    payload_b64, sig_b64 = tok.split(".", 1)
    # flip a char in the signature
    bad = payload_b64 + "." + ("A" if sig_b64[0] != "A" else "B") + sig_b64[1:]
    assert join_token.verify(bad) is None


def test_verify_rejects_expired():
    tok = join_token.issue("W", "n", "holder", ttl=-1)
    assert join_token.verify(tok) is None


def test_verify_rejects_garbage():
    assert join_token.verify(None) is None
    assert join_token.verify("") is None
    assert join_token.verify("nodot") is None
    assert join_token.verify("a.b") is None


def test_session_token_is_not_a_join_token(monkeypatch):
    """Domain separation: a session cookie can't be replayed as a join token."""
    monkeypatch.setenv("NDRCHST_SESSION_SECRET", "shared-secret")
    sess = auth_session.sign_session("WALLET1")
    assert join_token.verify(sess) is None
    # …and a join token isn't a valid session.
    tok = join_token.issue("WALLET1", "n", "gold")
    assert auth_session.verify_session(tok) is None


def test_tier_may_be_none():
    tok = join_token.issue("W", "n", None)
    assert join_token.verify(tok)["tier"] is None


def test_not_before_window_is_respected_via_exp():
    tok = join_token.issue("W", "n", "silver", ttl=2)
    assert join_token.verify(tok) is not None
    time.sleep(0)  # token still valid immediately
