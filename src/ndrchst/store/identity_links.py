"""Persistence for the ONLINE-MODE identity → wallet mapping (the Paper /
cross-play path).

The modded path derives an offline MC name from the wallet and gates on a
signed join token, so it lives entirely in [[wallet_links]]. The Paper path is
different: the player joins with their real, Mojang-authenticated UUID (or a
Bedrock xuid via Floodgate), which we can't derive from a wallet — so they link
it once (in-game `/link` + wallet sign-in) and we remember it here.

Tier is NOT stored here. It stays in `wallet_links` (the single Solana/holdings
source); the gate composes identity → wallet → wallet_links.tier. This table is
only the bridge between a real MC identity and a wallet.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IdentityLink:
    mc_uuid: str
    xuid: str | None
    username: str | None
    wallet: str
    linked_at: str


def _row(r: sqlite3.Row) -> IdentityLink:
    return IdentityLink(
        mc_uuid=r["mc_uuid"], xuid=r["xuid"], username=r["username"],
        wallet=r["wallet"], linked_at=r["linked_at"],
    )


def upsert(conn: sqlite3.Connection, mc_uuid: str, wallet: str, *,
           xuid: str | None = None, username: str | None = None) -> None:
    """Bind (or rebind) a real MC identity to a wallet. Keyed on mc_uuid, so a
    player relinking a different wallet overwrites cleanly; for Bedrock the
    Floodgate uuid is stable too."""
    conn.execute(
        """INSERT INTO identity_links (mc_uuid, xuid, username, wallet)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(mc_uuid) DO UPDATE SET
             xuid=excluded.xuid, username=excluded.username,
             wallet=excluded.wallet, linked_at=CURRENT_TIMESTAMP""",
        (mc_uuid, xuid, username, wallet),
    )


def get(conn: sqlite3.Connection, mc_uuid: str) -> IdentityLink | None:
    """The wallet linked to a real MC UUID, or None if unlinked."""
    cur = conn.execute(
        "SELECT * FROM identity_links WHERE mc_uuid = ?", (mc_uuid,))
    r = cur.fetchone()
    return _row(r) if r is not None else None


def get_by_xuid(conn: sqlite3.Connection, xuid: str) -> IdentityLink | None:
    """Lookup by Bedrock xuid (a Floodgate player can present a stable xuid even
    if its synthetic Java uuid is recomputed)."""
    cur = conn.execute(
        "SELECT * FROM identity_links WHERE xuid = ? ORDER BY linked_at DESC "
        "LIMIT 1", (xuid,))
    r = cur.fetchone()
    return _row(r) if r is not None else None


def unlink(conn: sqlite3.Connection, mc_uuid: str) -> None:
    """Drop an identity's wallet binding (player re-links from scratch)."""
    conn.execute("DELETE FROM identity_links WHERE mc_uuid = ?", (mc_uuid,))
