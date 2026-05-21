"""Unit tests for the one-time browser → installed-app handoff codes."""
from __future__ import annotations

import time

from ndrchst.domain import client_handoff


def test_mint_then_redeem_returns_wallet():
    code = client_handoff.mint("WalletAAA")
    assert client_handoff.redeem(code) == "WalletAAA"


def test_redeem_is_single_use():
    code = client_handoff.mint("WalletBBB")
    assert client_handoff.redeem(code) == "WalletBBB"
    assert client_handoff.redeem(code) is None  # consumed on first redeem


def test_unknown_code_is_none():
    assert client_handoff.redeem("never-minted") is None


def test_expired_code_is_none(monkeypatch):
    code = client_handoff.mint("WalletCCC")
    later = time.time() + client_handoff._TTL + 1
    monkeypatch.setattr(client_handoff.time, "time", lambda: later)
    assert client_handoff.redeem(code) is None


def test_codes_are_unguessable_and_distinct():
    a = client_handoff.mint("W")
    b = client_handoff.mint("W")
    assert a != b
    assert len(a) >= 24  # token_urlsafe(24) → no short/guessable codes
