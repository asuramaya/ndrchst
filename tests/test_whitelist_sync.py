"""Tests for wallet_links persistence and the admin-side RCON whitelist/rank
sync (with a fake RCON so no real server is needed)."""
from __future__ import annotations

from pathlib import Path

from ndrchst.runtime.whitelist_sync import sync_links_to_server
from ndrchst.store import wallet_links as wl
from ndrchst.store.db import connect
from ndrchst.store.wallet_links import WalletLink


def test_wallet_links_crud(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    wl.upsert(conn, "WALLET1", "EUr2Qn_pump", "silver", 0.7)
    link = wl.get(conn, "WALLET1")
    assert link is not None
    assert link.mc_name == "EUr2Qn_pump"
    assert link.tier == "silver"
    assert link.synced_at is None

    # upsert is idempotent on wallet and refreshes rank
    wl.upsert(conn, "WALLET1", "EUr2Qn_pump", "gold", 1.2)
    assert wl.get(conn, "WALLET1").tier == "gold"
    assert len(wl.list_all(conn)) == 1

    wl.mark_synced(conn, "WALLET1")
    assert wl.get(conn, "WALLET1").synced_at is not None


def _link(wallet: str, name: str, tier: str | None) -> WalletLink:
    return WalletLink(wallet, name, tier, 0.5, "now", None)


async def test_sync_whitelists_and_ranks():
    recorded: list[str] = []

    class FakeRCON:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def command(self, cmd: str) -> str:
            recorded.append(cmd)
            return ""

    links = [_link("W1", "alice", "silver"), _link("W2", "bob", None)]
    res = await sync_links_to_server(
        "127.0.0.1", 25575, "pw", links,
        rcon_factory=FakeRCON, rank_template="lp user {name} parent set {tier}")

    assert "whitelist add alice" in recorded
    assert "lp user alice parent set silver" in recorded
    assert "whitelist add bob" in recorded
    # bob has no tier -> no rank command for bob
    assert not any(c.startswith("lp user bob") for c in recorded)
    assert res[0].whitelisted and res[0].ranked
    assert res[1].whitelisted and not res[1].ranked


async def test_sync_records_rcon_errors():
    from ndrchst.runtime.rcon import RCONError

    class FailRCON:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def command(self, cmd: str) -> str:
            raise RCONError("server offline")

    res = await sync_links_to_server(
        "127.0.0.1", 25575, "pw", [_link("W", "carol", "gold")],
        rcon_factory=FailRCON, rank_template=None)
    assert not res[0].whitelisted
    assert res[0].error == "server offline"
