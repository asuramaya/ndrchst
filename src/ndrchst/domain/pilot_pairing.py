"""In-memory device-pairing for the desktop pilot's wallet sign-in.

OAuth device-flow shape: the pilot calls start() for a long secret `pair_id`
(which only it holds, used to poll) plus a short human `user_code`. The pilot
opens the web /link page with the user_code; the user connects their wallet
there and the server binds the verified pubkey to the code. The pilot polls
with its pair_id until the binding appears — it never sees a private key.

Single uvicorn worker -> in-memory is fine; a multi-worker deploy would need a
shared store. Pairings expire after _TTL.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

_TTL = 10 * 60  # 10 minutes
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no ambiguous 0/O/1/I


@dataclass
class Pairing:
    user_code: str
    exp: float
    pubkey: str | None = None  # set once the wallet is bound on the web side


_by_id: dict[str, Pairing] = {}
_code_to_id: dict[str, str] = {}


def _gc(now: float) -> None:
    for pid, p in list(_by_id.items()):
        if p.exp < now:
            _by_id.pop(pid, None)
            _code_to_id.pop(p.user_code, None)


def _new_code() -> str:
    s = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
    return f"{s[:4]}-{s[4:]}"


def start() -> tuple[str, str]:
    """Begin a pairing. Returns (pair_id, user_code)."""
    now = time.time()
    _gc(now)
    pair_id = secrets.token_urlsafe(32)
    code = _new_code()
    while code in _code_to_id:
        code = _new_code()
    _by_id[pair_id] = Pairing(user_code=code, exp=now + _TTL)
    _code_to_id[code] = pair_id
    return pair_id, code


def approve(user_code: str, pubkey: str) -> bool:
    """Bind a verified wallet to a pending pairing. True on success."""
    now = time.time()
    _gc(now)
    pid = _code_to_id.get(user_code.strip().upper())
    if not pid:
        return False
    p = _by_id.get(pid)
    if p is None or p.exp < now:
        return False
    p.pubkey = pubkey
    return True


def poll(pair_id: str) -> Pairing | None:
    """The pilot's view of its pairing (None if unknown/expired)."""
    now = time.time()
    _gc(now)
    p = _by_id.get(pair_id)
    if p is None or p.exp < now:
        return None
    return p
