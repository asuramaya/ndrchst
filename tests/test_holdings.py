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


def test_identity_known_wallet_does_no_live_rpc(tmp_path: Path, monkeypatch):
    """A wallet already in the DB serves its tier from the DB — zero Solana RPC —
    so /me + pairing-poll traffic can't drain a metered endpoint (Helius 1M/mo)."""
    calls = {"n": 0}

    def _boom(*_a, **_k):
        calls["n"] += 1
        return 9.9

    monkeypatch.setattr(solana, "try_holdings_pct", _boom)
    conn = connect(tmp_path / "t.db")
    wl.upsert(conn, "WKNOWN", "n", "gold", 1.2)

    ident = public._identity("WKNOWN", conn=conn)
    assert calls["n"] == 0           # known wallet → never hit RPC
    assert ident["holdings_pct"] == 1.2
    # A never-seen wallet on a display path (allow_live=False) also avoids RPC.
    assert public._identity("WUNSEEN", conn=conn, allow_live=False)["holdings_pct"] == 0.0
    assert calls["n"] == 0
    # Only a never-seen wallet on an establish path does exactly one live read.
    public._identity("WUNSEEN2", conn=conn)
    assert calls["n"] == 1


def test_refresh_holdings_if_stale_refreshes_then_throttles(tmp_path: Path, monkeypatch):
    """On launch/join the gate tier is re-read from chain, but at most once per
    cooldown per wallet so a crash-relaunch loop can't drain the RPC."""
    public._last_refresh.clear()
    monkeypatch.setattr(public, "_REFRESH_COOLDOWN", 300.0)
    calls = {"n": 0}

    def _read(*_a, **_k):
        calls["n"] += 1
        return 1.2  # → gold (>=1.0, <2.5)

    monkeypatch.setattr(solana, "try_holdings_pct", _read)
    conn = connect(tmp_path / "t.db")
    wl.upsert(conn, "WX", "wx", "holder", 0.0)  # stale gate tier

    public._refresh_holdings_if_stale("WX", conn)
    assert calls["n"] == 1
    assert wl.get(conn, "WX").holdings_pct == 1.2
    assert wl.get(conn, "WX").tier == "gold"          # gate tier refreshed
    assert wl.get(conn, "WX").snapshot_pct is None    # /daily snapshot untouched

    public._refresh_holdings_if_stale("WX", conn)     # within cooldown
    assert calls["n"] == 1                            # → no second RPC


def test_refresh_holdings_flaky_keeps_db_value(tmp_path: Path, monkeypatch):
    public._last_refresh.clear()
    monkeypatch.setattr(public, "_REFRESH_COOLDOWN", 0.0)  # cooldown off
    monkeypatch.setattr(solana, "try_holdings_pct", lambda *_a, **_k: None)
    conn = connect(tmp_path / "t.db")
    wl.upsert(conn, "WY", "wy", "gold", 1.2)
    public._refresh_holdings_if_stale("WY", conn)
    assert wl.get(conn, "WY").holdings_pct == 1.2     # flaky read keeps the value


def test_token_supply_is_cached(monkeypatch):
    solana._supply_cache.clear()
    calls = {"n": 0}

    def _fake_rpc(method, params, *, client, url):
        calls["n"] += 1
        return {"value": {"uiAmount": 1000.0}}

    monkeypatch.setattr(solana, "_rpc", _fake_rpc)
    a = solana.get_token_supply("MINT", client=None, url="URL")
    b = solana.get_token_supply("MINT", client=None, url="URL")
    assert a == b == 1000.0
    assert calls["n"] == 1                       # second read served from cache
    assert solana.get_token_supply("MINT", client=None, url="URL", use_cache=False) == 1000.0
    assert calls["n"] == 2                        # explicit fresh read bypasses cache


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
