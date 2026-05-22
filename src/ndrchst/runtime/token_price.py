"""Best-effort $NDRCHST price / market-cap for the site + in-game ticker.

This is a pure **decoration** — it does not have to be real-time or precise. It
is sourced from DexScreener (free, no API key) which is a wholly separate path
from the metered Solana RPC, so the ticker can NEVER burn the Helius cap. The
value is fetched on a slow background cadence, cached in-process on the box, and
read instantly by the page render + the in-game ``/tier`` / ``/price`` surfaces.

Semantics: stale-OK (we keep the last good value across a failed refresh), and
the ticker simply hides when there is nothing cached yet. NEVER calls Solana RPC.
"""
from __future__ import annotations

import logging
import threading
import time

import httpx

from .solana import token_mint

_log = logging.getLogger("ndrchst.price")

# DexScreener: GET .../tokens/<mint> → {"pairs": [{priceUsd, marketCap, fdv, …}]}
_DEX_URL = "https://api.dexscreener.com/latest/dex/tokens/"
_TIMEOUT = httpx.Timeout(8.0)

_lock = threading.Lock()
_cache: dict | None = None       # last good value (None until first success)
_fetched_at: float = 0.0


def fetch(*, mint_addr: str | None = None, client: httpx.Client | None = None) -> dict | None:
    """One DexScreener read → ``{price_usd, market_cap, symbol, url}`` or None.
    No caching; the most-liquid pair wins. Never raises."""
    addr = mint_addr or token_mint()
    c = client or httpx.Client(timeout=_TIMEOUT, headers={"user-agent": "ndrchst"})
    try:
        resp = c.get(_DEX_URL + addr)
        resp.raise_for_status()
        pairs = (resp.json() or {}).get("pairs") or []
        if not pairs:
            return None
        best = max(pairs, key=lambda p: ((p.get("liquidity") or {}).get("usd") or 0))
        price = best.get("priceUsd")
        base = best.get("baseToken") or {}
        return {
            "price_usd": float(price) if price else None,
            "market_cap": best.get("marketCap") or best.get("fdv"),
            "symbol": base.get("symbol") or "NDRCHST",
            "url": best.get("url"),
        }
    except Exception:
        return None
    finally:
        if client is None:
            c.close()


def refresh() -> dict | None:
    """Fetch once and update the cache; keep the last good value on failure.
    Called by the box's slow background loop (and lazily in tests)."""
    global _cache, _fetched_at
    val = fetch()
    if val is not None:
        with _lock:
            _cache = val
            _fetched_at = time.time()
    return get()


def get() -> dict | None:
    """The cached ticker value (+ ``age_s``), or None if nothing cached yet.
    Never blocks on the network — this is what page render + ``/tier`` read."""
    with _lock:
        if _cache is None:
            return None
        return {**_cache, "age_s": int(time.time() - _fetched_at)}


def _reset_for_tests() -> None:
    global _cache, _fetched_at
    with _lock:
        _cache = None
        _fetched_at = 0.0
