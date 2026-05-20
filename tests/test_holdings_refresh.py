"""Tests for the holdings refresh loop (recompute tier from current chain
holdings). Network is stubbed via an injected holdings_fn."""
from __future__ import annotations

from pathlib import Path

from ndrchst.runtime.holdings_refresh import refresh_all_holdings
from ndrchst.store import wallet_links as wl
from ndrchst.store.db import connect


def test_refresh_recomputes_and_persists_tiers(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    # A whale who has since sold down to bronze, and a holder who bought in.
    wl.upsert(conn, "WHALE", "Whale_name", "whale", 6.0)
    wl.upsert(conn, "BUYER", "Buyer_name", None, 0.0)

    holdings = {"WHALE": 0.2, "BUYER": 1.3}  # whale -> bronze, buyer -> gold
    results = refresh_all_holdings(conn, holdings_fn=lambda w: holdings[w])

    by_wallet = {r.wallet: r for r in results}
    assert by_wallet["WHALE"].old_tier == "whale"
    assert by_wallet["WHALE"].new_tier == "bronze"
    assert by_wallet["WHALE"].changed
    assert by_wallet["BUYER"].old_tier is None
    assert by_wallet["BUYER"].new_tier == "gold"

    # Persisted.
    assert wl.get(conn, "WHALE").tier == "bronze"
    assert wl.get(conn, "WHALE").holdings_pct == 0.2
    assert wl.get(conn, "BUYER").tier == "gold"


def test_refresh_drops_rank_when_sold_out(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    wl.upsert(conn, "EXHOLDER", "Ex_name", "silver", 0.7)
    results = refresh_all_holdings(conn, holdings_fn=lambda w: 0.0)
    # Selling out drops to the base 'holder' floor, not off the ladder.
    assert results[0].new_tier == "holder"
    link = wl.get(conn, "EXHOLDER")
    assert link.tier == "holder"
    # The hourly job also writes the carousel-proof snapshot.
    assert link.snapshot_tier == "holder"
    assert link.snapshot_at is not None


def test_refresh_empty_is_noop(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    assert refresh_all_holdings(conn, holdings_fn=lambda w: 1.0) == []
