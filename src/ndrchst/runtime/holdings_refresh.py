"""Re-read every linked wallet's on-chain holdings and recompute its rank.

Holdings are captured once at sign-in/link time and then go stale — a wallet
that sells $NDRCHST keeps its old tier until something re-reads the chain.
This closes that loop: refresh recomputes each wallet's tier from current
holdings and persists it, so the next admin whitelist/rank sync pushes the
corrected rank to the game server.

Admin-side, single-operator (one shared httpx client across all wallets). A
flaky RPC read for a wallet is SKIPPED, not written as 0% — the snapshot is the
durable basis for daily rewards and the identity fallback, so wiping it on a
transient miss is what made holder detection flap. A genuine zero balance still
reads 0.0 and is recorded.
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
    holdings_fn: Callable[..., float | None] | None = None,
) -> list[RefreshResult]:
    """Recompute + persist tier for every linked wallet. `holdings_fn` is
    injectable for tests; in production it's solana.try_holdings_pct over a
    single shared client so we don't reopen a connection per wallet. A None
    return (flaky RPC) skips that wallet, preserving its last-known tier."""
    links = wl.list_all(conn)
    if not links:
        return []
    results: list[RefreshResult] = []
    client = None if holdings_fn else httpx.Client(timeout=solana._TIMEOUT)
    try:
        fn = holdings_fn or (lambda w: solana.try_holdings_pct(w, client=client))
        for link in links:
            pct = fn(link.wallet)
            if pct is None:
                # Flaky read — keep this wallet's last-known tier + snapshot
                # rather than wiping it to 0. Skip rather than corrupt.
                continue
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
