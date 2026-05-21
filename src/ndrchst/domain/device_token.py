"""Long-lived device tokens — the durable credential the client carries.

The auth-first flow: a wallet signs in on the web (SIWS), downloads a client
with a device token baked in, and from then on the client exchanges that device
token for a *fresh* short-lived join token at each launch (see join_token) —
no in-launcher device-flow round-trip, and the join token can't go stale during
a long install. The device token is the only thing the client stores; it's
revocable (rotate NDRCHST_SESSION_SECRET) and re-readable only by the box.

Stdlib HMAC, same secret as auth_session/join_token, domain-separated prefix.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

_DEVICE_TTL = 30 * 24 * 3600  # 30 days — re-download to refresh
_PREFIX = b"ndrchst-device-v1:"
_fallback_secret = secrets.token_hex(32)


def _secret() -> bytes:
    return os.environ.get("NDRCHST_SESSION_SECRET", _fallback_secret).encode()


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue(wallet: str, *, ttl: int = _DEVICE_TTL) -> str:
    payload = json.dumps(
        {"w": wallet, "exp": int(time.time()) + ttl},
        separators=(",", ":"),
    ).encode()
    sig = hmac.new(_secret(), _PREFIX + payload, hashlib.sha256).digest()
    return f"{_b64e(payload)}.{_b64e(sig)}"


def verify(token: str | None) -> str | None:
    """Return the bound wallet if the token is valid + unexpired, else None."""
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
        return wallet if isinstance(wallet, str) else None
    except (ValueError, KeyError, json.JSONDecodeError):
        return None
