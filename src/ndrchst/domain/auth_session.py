"""Stateless HMAC-signed sessions + single-use login nonces — stdlib only.

A session token is `base64url(payload).base64url(hmac_sha256(secret, payload))`
where payload is JSON `{"pk": <wallet>, "exp": <unix>}`. No JWT library; the
secret comes from NDRCHST_SESSION_SECRET (a random per-process secret is used
as a fallback so dev still works, but then sessions don't survive a restart).

Nonces are held in-process with a TTL. The public surface runs as a single
uvicorn worker, so an in-memory store is sufficient; a multi-worker deploy
would need a shared store (Redis/SQLite) — noted, not built.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

_SESSION_TTL = 7 * 24 * 3600  # 7 days
_NONCE_TTL = 5 * 60  # 5 minutes
_DOMAIN = "ndrchst"

_nonces: dict[str, float] = {}  # nonce -> expiry (unix)
_fallback_secret = secrets.token_hex(32)


def _secret() -> bytes:
    return os.environ.get("NDRCHST_SESSION_SECRET", _fallback_secret).encode()


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# --- sessions ----------------------------------------------------------------
def sign_session(pubkey: str, *, ttl: int = _SESSION_TTL) -> str:
    payload = json.dumps({"pk": pubkey, "exp": int(time.time()) + ttl},
                         separators=(",", ":")).encode()
    sig = hmac.new(_secret(), payload, hashlib.sha256).digest()
    return f"{_b64e(payload)}.{_b64e(sig)}"


def verify_session(token: str | None) -> str | None:
    """Return the wallet pubkey if the token is valid and unexpired, else None."""
    if not token or "." not in token:
        return None
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = _b64d(payload_b64)
        expected = hmac.new(_secret(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64d(sig_b64)):
            return None
        data = json.loads(payload)
        if int(data.get("exp", 0)) < time.time():
            return None
        pk = data.get("pk")
        return pk if isinstance(pk, str) else None
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


# --- nonces ------------------------------------------------------------------
def _gc(now: float) -> None:
    for n, exp in list(_nonces.items()):
        if exp < now:
            _nonces.pop(n, None)


def issue_nonce() -> str:
    now = time.time()
    _gc(now)
    nonce = secrets.token_urlsafe(16)
    _nonces[nonce] = now + _NONCE_TTL
    return nonce


def consume_nonce(nonce: str) -> bool:
    """True iff `nonce` was issued, unexpired, and unused. Single-use."""
    now = time.time()
    exp = _nonces.pop(nonce, None)
    return exp is not None and exp >= now


# --- SIWS challenge message --------------------------------------------------
def build_message(pubkey: str, nonce: str, *, domain: str = _DOMAIN,
                  uri: str = "https://www.ndrchst.com") -> str:
    """The human-readable challenge the wallet signs. The client rebuilds the
    same text and signs it; the server re-derives it from the issued nonce so
    the signed bytes are pinned to our domain + statement, not attacker text."""
    return (
        f"{domain} wants you to sign in with your Solana account:\n"
        f"{pubkey}\n\n"
        f"Sign in to ndrchst — your wallet is your identity and your rank.\n\n"
        f"URI: {uri}\n"
        f"Nonce: {nonce}"
    )
