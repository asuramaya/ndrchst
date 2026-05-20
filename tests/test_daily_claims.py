"""Durable, wallet-keyed daily cooldown."""
from __future__ import annotations

from pathlib import Path

from ndrchst.store import daily_claims as dc
from ndrchst.store.db import connect


def test_first_claim_succeeds_then_cooldown(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    ok, left = dc.try_claim(conn, "WALLET1")
    assert ok and left == 0
    # Immediate re-claim is on cooldown with ~24h remaining.
    ok2, left2 = dc.try_claim(conn, "WALLET1")
    assert not ok2
    assert 0 < left2 <= dc.COOLDOWN_SECONDS


def test_cooldown_is_per_wallet(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    assert dc.try_claim(conn, "A")[0] is True
    # A different wallet is unaffected by A's claim.
    assert dc.try_claim(conn, "B")[0] is True


def test_reset_clears_cooldown(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    assert dc.try_claim(conn, "W")[0] is True
    assert dc.try_claim(conn, "W")[0] is False
    dc.reset(conn, "W")
    assert dc.try_claim(conn, "W")[0] is True


def test_zero_cooldown_allows_immediate_reclaim(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    assert dc.try_claim(conn, "W", cooldown_s=0)[0] is True
    assert dc.try_claim(conn, "W", cooldown_s=0)[0] is True
