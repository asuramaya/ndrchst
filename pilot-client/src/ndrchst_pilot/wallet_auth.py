"""Wallet sign-in for the pilot via the web pairing flow (OAuth device-flow
shape). The pilot never sees a private key: it starts a pairing, opens the
browser to the /link page, and polls until the user connects + signs their
Solana wallet there. Stdlib only (urllib) to keep the frozen build lean.
"""
from __future__ import annotations

import contextlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass

DEFAULT_BASE = "https://play.ndrchst.com"

# Cloudflare's WAF 403s the default `Python-urllib/x.y` UA in front of the
# public surface, so present a browser-ish one (same trick as modpack.py).
_UA = "Mozilla/5.0 (ndrchst-pilot)"


@dataclass(frozen=True, slots=True)
class WalletIdentity:
    wallet: str
    display: str
    mc_name: str
    tier: str | None
    tier_name: str | None
    holdings_pct: float
    join_token: str  # short-lived credential the ndrchst-auth mod presents


class WalletAuthError(Exception):
    """Sign-in could not complete (timeout, network, or cancelled)."""


def _post_json(url: str, payload: dict | None = None, timeout: float = 15) -> dict:
    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"content-type": "application/json", "user-agent": _UA},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _get_json(url: str, timeout: float = 15) -> dict:
    req = urllib.request.Request(url, headers={"user-agent": _UA}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def begin(
    base_url: str | None,
    *,
    on_log: Callable[[str], None] = lambda _s: None,
    open_browser: bool = True,
) -> WalletIdentity:
    """Run the pairing flow to completion, returning the linked wallet
    identity. Raises WalletAuthError on timeout/failure."""
    base = (base_url or DEFAULT_BASE).rstrip("/")
    try:
        start = _post_json(f"{base}/pilot/auth/start")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise WalletAuthError(f"could not reach sign-in service: {e}") from e

    pair_id = start["pair_id"]
    code = start["user_code"]
    verify_url = start.get("verify_url") or f"{base}/link?code={code}"
    interval = max(1, int(start.get("interval", 2)))
    deadline = time.time() + int(start.get("expires_in", 600))

    on_log(f"Sign in to play — pairing code {code}")
    if open_browser:
        with contextlib.suppress(Exception):
            webbrowser.open(verify_url)
    on_log(f"If the browser didn't open, visit: {verify_url}")

    poll_url = f"{base}/pilot/auth/poll?pair_id={urllib.parse.quote(pair_id)}"
    while time.time() < deadline:
        time.sleep(interval)
        try:
            poll = _get_json(poll_url)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise WalletAuthError("pairing expired before sign-in") from e
            continue
        except (urllib.error.URLError, OSError, ValueError):
            continue
        if poll.get("status") == "approved":
            on_log(f"Linked wallet {poll.get('display')}")
            return WalletIdentity(
                wallet=poll["wallet"],
                display=poll.get("display", ""),
                mc_name=poll.get("mc_name", ""),
                tier=poll.get("tier"),
                tier_name=poll.get("tier_name"),
                holdings_pct=float(poll.get("holdings_pct", 0.0)),
                join_token=poll.get("join_token", ""),
            )
    raise WalletAuthError("sign-in timed out")
