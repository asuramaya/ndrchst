"""One-time handoff codes for the browser → installed-app deep link.

When a wallet is already signed in on the web, the play page mints a short-lived,
single-use handoff code (POST /me/handoff) and embeds it in a ``ndrchst://`` deep
link. The installed client redeems it (POST /client/auth/handoff) for a device
token — so a freshly downloaded executable links to the wallet without a second
browser round-trip, and **no durable credential ever travels in the URL** (only
the one-time code does, and it's useless once redeemed or after _TTL).

In-memory (single uvicorn worker, same as client_pairing); a multi-worker deploy
would need a shared store. Codes expire after _TTL and are consumed on first
redeem.
"""
from __future__ import annotations

import secrets
import time

_TTL = 180  # 3 minutes — just long enough to click through into the app
_codes: dict[str, tuple[str, float]] = {}  # code -> (wallet, expiry)


def _gc(now: float) -> None:
    for code, (_w, exp) in list(_codes.items()):
        if exp < now:
            _codes.pop(code, None)


def mint(wallet: str) -> str:
    """Issue a one-time handoff code bound to a wallet."""
    now = time.time()
    _gc(now)
    code = secrets.token_urlsafe(24)
    _codes[code] = (wallet, now + _TTL)
    return code


def redeem(code: str) -> str | None:
    """Consume a code, returning its bound wallet (None if unknown/expired).

    Single-use: a valid code is removed on first redeem, so a leaked URL can't
    be replayed."""
    now = time.time()
    _gc(now)
    entry = _codes.pop(code, None)
    if entry is None:
        return None
    wallet, exp = entry
    return wallet if exp >= now else None
