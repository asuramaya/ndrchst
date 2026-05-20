"""Persistence for wallet identities (the bridge between the public auth
surface and the admin RCON sync). The public surface upserts on sign-in;
the admin surface reads to push whitelist + rank to game servers.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WalletLink:
    wallet: str
    mc_name: str
    tier: str | None
    holdings_pct: float
    snapshot_tier: str | None
    snapshot_pct: float | None
    snapshot_at: str | None
    linked_at: str
    synced_at: str | None


def _row(r: sqlite3.Row) -> WalletLink:
    return WalletLink(
        wallet=r["wallet"], mc_name=r["mc_name"], tier=r["tier"],
        holdings_pct=r["holdings_pct"],
        snapshot_tier=r["snapshot_tier"], snapshot_pct=r["snapshot_pct"],
        snapshot_at=r["snapshot_at"],
        linked_at=r["linked_at"], synced_at=r["synced_at"],
    )


def upsert(conn: sqlite3.Connection, wallet: str, mc_name: str,
           tier: str | None, holdings_pct: float) -> None:
    """Record (or refresh) a wallet's identity + current rank."""
    conn.execute(
        """INSERT INTO wallet_links (wallet, mc_name, tier, holdings_pct)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(wallet) DO UPDATE SET
             mc_name=excluded.mc_name, tier=excluded.tier,
             holdings_pct=excluded.holdings_pct""",
        (wallet, mc_name, tier, holdings_pct),
    )


def set_snapshot(conn: sqlite3.Connection, wallet: str,
                 snapshot_tier: str | None, snapshot_pct: float) -> None:
    """Record the hourly holdings snapshot for a wallet. Distinct from `upsert`
    (which writes the on-demand latest values) so daily rewards read a tier the
    user can't refresh on demand — only the hourly job calls this."""
    conn.execute(
        """UPDATE wallet_links
           SET snapshot_tier = ?, snapshot_pct = ?, snapshot_at = CURRENT_TIMESTAMP
           WHERE wallet = ?""",
        (snapshot_tier, snapshot_pct, wallet),
    )


def get(conn: sqlite3.Connection, wallet: str) -> WalletLink | None:
    r = conn.execute("SELECT * FROM wallet_links WHERE wallet = ?", (wallet,)).fetchone()
    return _row(r) if r else None


def list_all(conn: sqlite3.Connection) -> list[WalletLink]:
    rows = conn.execute("SELECT * FROM wallet_links ORDER BY linked_at").fetchall()
    return [_row(r) for r in rows]


def mark_synced(conn: sqlite3.Connection, wallet: str) -> None:
    conn.execute(
        "UPDATE wallet_links SET synced_at = CURRENT_TIMESTAMP WHERE wallet = ?",
        (wallet,),
    )
