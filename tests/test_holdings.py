"""Holder detection must not flap on a flaky RPC.

Two guarantees: a failed read never overwrites a wallet's snapshot in the hourly
refresh, and a failed live read in _identity falls back to that snapshot instead
of demoting the holder to 0.
"""
from __future__ import annotations

from pathlib import Path

from ndrchst import public
from ndrchst.domain import wallet
from ndrchst.runtime import holdings_refresh, solana
from ndrchst.store import wallet_links as wl
from ndrchst.store.db import connect


def test_refresh_skips_wallet_on_flaky_read(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    wl.upsert(conn, "W1", "name1", "silver", 2.0)
    wl.set_snapshot(conn, "W1", "silver", 2.0)
    # A flaky read (None) must NOT wipe the snapshot/tier.
    res = holdings_refresh.refresh_all_holdings(conn, holdings_fn=lambda _w: None)
    assert res == []  # nothing recorded
    link = wl.get(conn, "W1")
    assert link.tier == "silver"
    assert link.snapshot_pct == 2.0  # preserved, not zeroed


def test_refresh_records_genuine_zero(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    wl.upsert(conn, "W2", "name2", "silver", 2.0)
    wl.set_snapshot(conn, "W2", "silver", 2.0)
    # A real zero (sold out) IS recorded — only flaky None is skipped.
    holdings_refresh.refresh_all_holdings(conn, holdings_fn=lambda _w: 0.0)
    assert wl.get(conn, "W2").snapshot_pct == 0.0


def test_identity_falls_back_to_snapshot_on_flaky_rpc(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(solana, "try_holdings_pct", lambda *_a, **_k: None)
    conn = connect(tmp_path / "t.db")
    wl.upsert(conn, "WHODLER", "n", "silver", 3.0)
    wl.set_snapshot(conn, "WHODLER", "silver", 3.0)
    # With the conn, a flaky live read reuses the snapshot → tier holds.
    ident = public._identity("WHODLER", conn=conn)
    assert ident["holdings_pct"] == 3.0
    assert ident["tier"] == (wallet.tier_for(3.0).key if wallet.tier_for(3.0) else None)
    # Without a conn there's no fallback source → degrades to 0.0 (old behaviour).
    assert public._identity("WHODLER")["holdings_pct"] == 0.0
