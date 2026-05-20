"""Durable, wallet-keyed daily-claim cooldown.

The mod used to hold the 24h cooldown in memory, so every server restart let
everyone re-claim. This moves it to the box's SQLite so it survives reboots and
is scoped to the wallet (not a per-process map). `try_claim` is an atomic
check-and-set under an IMMEDIATE transaction so a double-click can't double-claim.
"""
from __future__ import annotations

import sqlite3

COOLDOWN_SECONDS = 24 * 3600


def try_claim(conn: sqlite3.Connection, wallet: str,
              *, cooldown_s: int = COOLDOWN_SECONDS) -> tuple[bool, int]:
    """Atomically claim the daily for `wallet`. Returns (ok, seconds_left):
    (True, 0) if the claim succeeded (and is now recorded), or
    (False, seconds_left) if still on cooldown."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT CAST(strftime('%s','now') - strftime('%s', claimed_at) AS INTEGER) "
            "AS elapsed FROM daily_claims WHERE wallet = ?",
            (wallet,),
        ).fetchone()
        if row is not None and row["elapsed"] < cooldown_s:
            conn.execute("COMMIT")
            return False, cooldown_s - int(row["elapsed"])
        conn.execute(
            "INSERT INTO daily_claims (wallet, claimed_at) VALUES (?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(wallet) DO UPDATE SET claimed_at = CURRENT_TIMESTAMP",
            (wallet,),
        )
        conn.execute("COMMIT")
        return True, 0
    except Exception:
        conn.execute("ROLLBACK")
        raise


def reset(conn: sqlite3.Connection, wallet: str) -> None:
    """Clear a wallet's cooldown (the op `/ndrchst daily reset` escape hatch)."""
    conn.execute("DELETE FROM daily_claims WHERE wallet = ?", (wallet,))
