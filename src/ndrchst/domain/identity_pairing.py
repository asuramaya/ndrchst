"""In-memory pairing for the ONLINE-MODE /link flow (Paper / cross-play path).

Mirrors [[client_pairing]] but the pairing carries the player's real MC identity
(uuid/xuid/username) instead of being initiated by a desktop client: the player
runs `/link` in-game, the Paper plugin calls start() with their authenticated
identity and shows them the code, they approve it on the web /link page by
signing with their wallet, and the binding becomes an identity_links row.

Distinct from client_pairing because the payloads differ (identity vs nothing)
and approve resolves to an identity→wallet write, not a join token. Single
uvicorn worker → in-memory is fine; pairings expire after _TTL.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field

_TTL = 10 * 60  # 10 minutes
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no ambiguous 0/O/1/I


@dataclass
class Pairing:
    user_code: str
    exp: float
    identity: dict = field(default_factory=dict)  # {uuid, xuid?, username?}
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


def start(identity: dict) -> tuple[str, str]:
    """Begin a link. `identity` is the authenticated {uuid, xuid?, username?}.
    Returns (pair_id, user_code)."""
    now = time.time()
    _gc(now)
    pair_id = secrets.token_urlsafe(32)
    code = _new_code()
    while code in _code_to_id:
        code = _new_code()
    _by_id[pair_id] = Pairing(user_code=code, exp=now + _TTL, identity=identity)
    _code_to_id[code] = pair_id
    return pair_id, code


def approve(user_code: str, pubkey: str) -> dict | None:
    """Bind a verified wallet to a pending link. Returns the stored identity to
    persist (or None if the code is unknown/expired)."""
    now = time.time()
    _gc(now)
    pid = _code_to_id.get(user_code.strip().upper())
    if not pid:
        return None
    p = _by_id.get(pid)
    if p is None or p.exp < now:
        return None
    p.pubkey = pubkey
    return p.identity


def poll(pair_id: str) -> Pairing | None:
    """The plugin's view of a pending link (None if unknown/expired)."""
    now = time.time()
    _gc(now)
    p = _by_id.get(pair_id)
    if p is None or p.exp < now:
        return None
    return p


def _reset_for_tests() -> None:
    _by_id.clear()
    _code_to_id.clear()
