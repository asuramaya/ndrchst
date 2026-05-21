"""Short-lived, HMAC-signed join tokens — the cryptographic gate for the
modded server.

After a wallet completes Sign-In-With-Solana on the web surface, the box mints
a join token binding `{wallet, mc_name, tier}` with a short expiry. The client
carries it into the game dir; the `ndrchst-auth` NeoForge mod sends it during
the connection handshake and the server side POSTs it back to `/join/verify`.
A client with no valid token never gets onto the server — the client becomes
the only way in, and the token's `mc_name` binding stops anyone joining under
someone else's wallet handle.

Stdlib only. Uses the same signing secret as auth_session
(`NDRCHST_SESSION_SECRET`) with a domain-separation prefix so a session cookie
can never be replayed as a join token (or vice versa).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

_JOIN_TTL = 30 * 60  # 30 min — sign in, then press Play. v2: a refresh path.
_PREFIX = b"ndrchst-join-v1:"  # domain separation from auth_session tokens
_fallback_secret = secrets.token_hex(32)


def _secret() -> bytes:
    return os.environ.get("NDRCHST_SESSION_SECRET", _fallback_secret).encode()


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue(wallet: str, mc_name: str, tier: str | None, *, ttl: int = _JOIN_TTL) -> str:
    payload = json.dumps(
        {"w": wallet, "n": mc_name, "t": tier, "exp": int(time.time()) + ttl},
        separators=(",", ":"),
    ).encode()
    sig = hmac.new(_secret(), _PREFIX + payload, hashlib.sha256).digest()
    return f"{_b64e(payload)}.{_b64e(sig)}"


def verify(token: str | None) -> dict | None:
    """Return `{wallet, mc_name, tier}` if the token is well-formed, untampered,
    and unexpired, else None."""
    if not token or "." not in token:
        return None
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = _b64d(payload_b64)
        expected = hmac.new(_secret(), _PREFIX + payload, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64d(sig_b64)):
            return None
        data = json.loads(payload)
        if int(data.get("exp", 0)) < time.time():
            return None
        wallet = data.get("w")
        mc_name = data.get("n")
        if not isinstance(wallet, str) or not isinstance(mc_name, str):
            return None
        return {"wallet": wallet, "mc_name": mc_name, "tier": data.get("t")}
    except (ValueError, KeyError, json.JSONDecodeError):
        return None
