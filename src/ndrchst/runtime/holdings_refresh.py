"""Re-read every linked wallet's on-chain holdings and recompute its rank.

Holdings are captured once at sign-in/link time and then go stale — a wallet
that sells $NDRCHST keeps its old tier until something re-reads the chain.
This closes that loop: refresh recomputes each wallet's tier from current
holdings and persists it, so the next admin whitelist/rank sync pushes the
corrected rank to the game server.

Admin-side, single-operator (one shared httpx client across all wallets). RPC
misses degrade to 0% per wallet (same contract as solana.holdings_pct) — a
flaky RPC never wipes someone's rank by accident beyond that one read.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import httpx

from ..domain import wallet
from ..store import wallet_links as wl
from . import solana


@dataclass(frozen=True, slots=True)
class RefreshResult:
    wallet: str
    old_tier: str | None
    new_tier: str | None
    holdings_pct: float

    @property
    def changed(self) -> bool:
        return self.old_tier != self.new_tier


def refresh_all_holdings(
    conn,
    *,
    holdings_fn: Callable[..., float] | None = None,
) -> list[RefreshResult]:
    """Recompute + persist tier for every linked wallet. `holdings_fn` is
    injectable for tests; in production it's solana.holdings_pct over a single
    shared client so we don't reopen a connection per wallet."""
    links = wl.list_all(conn)
    if not links:
        return []
    results: list[RefreshResult] = []
    client = None if holdings_fn else httpx.Client(timeout=solana._TIMEOUT)
    try:
        fn = holdings_fn or (lambda w: solana.holdings_pct(w, client=client))
        for link in links:
            pct = fn(link.wallet)
            tier = wallet.tier_for(pct)
            new_key = tier.key if tier else None
            # Latest values (display + join-gate rank) AND the hourly snapshot
            # (the carousel-proof basis for daily rewards) move together here —
            # this job is the only writer of the snapshot.
            wl.upsert(conn, link.wallet, link.mc_name, new_key, pct)
            wl.set_snapshot(conn, link.wallet, new_key, pct)
            results.append(RefreshResult(link.wallet, link.tier, new_key, pct))
    finally:
        if client is not None:
            client.close()
    return results
