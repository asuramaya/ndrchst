"""Read a wallet's $NDRCHST holdings from Solana via raw JSON-RPC.

No solana-py / solders dependency — two stdlib-shaped JSON-RPC calls over
httpx (already a dep): `getTokenSupply` for the mint's total supply and
`getTokenAccountsByOwner` for a wallet's balance. holdings_pct() turns that
into a percentage of supply, which `domain.wallet.tier_for` maps to a rank.

Config is environment-driven:
  NDRCHST_SOLANA_RPC   default https://api.mainnet-beta.solana.com
  NDRCHST_TOKEN_MINT   default the $NDRCHST pump.fun mint
"""
from __future__ import annotations

import os

import httpx

DEFAULT_RPC = "https://api.mainnet-beta.solana.com"
DEFAULT_MINT = "EUr2QnpmavMw51JiFYeTRnUywY7mPAtouzyY2P21pump"

_TIMEOUT = httpx.Timeout(connect=10.0, read=15.0, write=10.0, pool=10.0)


def rpc_url() -> str:
    return os.environ.get("NDRCHST_SOLANA_RPC", DEFAULT_RPC)


def token_mint() -> str:
    return os.environ.get("NDRCHST_TOKEN_MINT", DEFAULT_MINT)


def _rpc(method: str, params: list, *, client: httpx.Client, url: str) -> dict:
    r = client.post(
        url,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
    )
    r.raise_for_status()
    body = r.json()
    if "error" in body:
        raise RuntimeError(f"solana rpc {method}: {body['error']}")
    return body.get("result", {})


def get_token_supply(mint: str, *, client: httpx.Client, url: str) -> float:
    """Total supply of the mint as a UI amount (decimals applied)."""
    res = _rpc("getTokenSupply", [mint], client=client, url=url)
    return float(res.get("value", {}).get("uiAmount") or 0.0)


def get_balance(owner: str, mint: str, *, client: httpx.Client, url: str) -> float:
    """Sum of a wallet's token accounts for `mint`, as a UI amount."""
    res = _rpc(
        "getTokenAccountsByOwner",
        [owner, {"mint": mint}, {"encoding": "jsonParsed"}],
        client=client,
        url=url,
    )
    total = 0.0
    for acct in res.get("value", []):
        info = acct.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
        amt = info.get("tokenAmount", {}).get("uiAmount")
        if amt:
            total += float(amt)
    return total


def try_holdings_pct(
    owner: str,
    *,
    mint: str | None = None,
    url: str | None = None,
    client: httpx.Client | None = None,
) -> float | None:
    """A wallet's holdings as a percentage of total supply, or **None if the RPC
    read failed** (network / rate-limit / bad response).

    The None-vs-0.0 distinction is the point: a *successful* read of an empty
    balance is a real 0.0, but a *flaky* read is None — so callers can fall back
    to a last-known snapshot instead of silently demoting a holder's rank. A
    flaky RPC must never block login or wipe someone's tier."""
    mint = mint or token_mint()
    url = url or rpc_url()
    owns = client is None
    c = client or httpx.Client(timeout=_TIMEOUT)
    try:
        supply = get_token_supply(mint, client=c, url=url)
        if supply <= 0:
            return 0.0
        bal = get_balance(owner, mint, client=c, url=url)
        return (bal / supply) * 100.0
    except (httpx.HTTPError, RuntimeError, ValueError):
        return None
    finally:
        if owns:
            c.close()


def holdings_pct(
    owner: str,
    *,
    mint: str | None = None,
    url: str | None = None,
    client: httpx.Client | None = None,
) -> float:
    """Back-compat wrapper: holdings as a % of supply, 0.0 on any miss. Prefer
    :func:`try_holdings_pct` where a flaky read should preserve a prior value."""
    pct = try_holdings_pct(owner, mint=mint, url=url, client=client)
    return pct if pct is not None else 0.0
