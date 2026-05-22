"""Wallet identity for the ndrchst stack — Solana ed25519 signature
verification, base58 decoding, display/MC-name derivation, and the
holdings -> rank tier mapping.

No third-party crypto dependency: ed25519 *verification* is a vendored
pure-Python implementation of RFC 8032 (verify-only — we never hold or
touch a secret key, so constant-time isn't a concern). base58 decode is
hand-rolled. See the project memory `project_oss_monorepo` for the
dependency rationale (everything in-repo, no web3/Solana SDK).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

# --- base58 (Bitcoin/Solana alphabet) ----------------------------------------
_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {c: i for i, c in enumerate(_B58_ALPHABET)}


def b58decode(s: str) -> bytes:
    """Decode a base58 string to bytes. Raises ValueError on bad input."""
    if not s:
        raise ValueError("empty base58 string")
    num = 0
    for ch in s:
        v = _B58_INDEX.get(ch)
        if v is None:
            raise ValueError(f"invalid base58 character: {ch!r}")
        num = num * 58 + v
    n_leading = len(s) - len(s.lstrip("1"))  # each leading '1' is a zero byte
    body = num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b""
    return b"\x00" * n_leading + body


# --- ed25519 verify (RFC 8032, vendored, verify-only) ------------------------
_Q = 2**255 - 19


def _inv(x: int) -> int:
    return pow(x, _Q - 2, _Q)


_D = (-121665 * _inv(121666)) % _Q
_I = pow(2, (_Q - 1) // 4, _Q)


def _xrecover(y: int) -> int:
    xx = (y * y - 1) * _inv(_D * y * y + 1) % _Q
    x = pow(xx, (_Q + 3) // 8, _Q)
    if (x * x - xx) % _Q != 0:
        x = (x * _I) % _Q
    if x % 2 != 0:
        x = _Q - x
    return x


_By = (4 * _inv(5)) % _Q
_B = (_xrecover(_By) % _Q, _By % _Q)


def _edwards_add(p: tuple[int, int], q: tuple[int, int]) -> tuple[int, int]:
    x1, y1 = p
    x2, y2 = q
    denom = _D * x1 * x2 * y1 * y2
    x3 = (x1 * y2 + x2 * y1) * _inv(1 + denom) % _Q
    y3 = (y1 * y2 + x1 * x2) * _inv(1 - denom) % _Q
    return (x3 % _Q, y3 % _Q)


def _scalarmult(p: tuple[int, int], e: int) -> tuple[int, int]:
    result = (0, 1)  # neutral element
    while e > 0:
        if e & 1:
            result = _edwards_add(result, p)
        p = _edwards_add(p, p)
        e >>= 1
    return result


def _isoncurve(p: tuple[int, int]) -> bool:
    x, y = p
    return (-x * x + y * y - 1 - _D * x * x * y * y) % _Q == 0


def _decodepoint(s: bytes) -> tuple[int, int]:
    y = int.from_bytes(s, "little") & ((1 << 255) - 1)
    x = _xrecover(y)
    sign = (s[31] >> 7) & 1
    if (x & 1) != sign:
        x = _Q - x
    p = (x, y)
    if not _isoncurve(p):
        raise ValueError("point not on curve")
    return p


def ed25519_verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """True iff `signature` is a valid ed25519 signature of `message` by
    `public_key` (32 bytes). Never raises."""
    if len(signature) != 64 or len(public_key) != 32:
        return False
    try:
        rr = _decodepoint(signature[:32])
        aa = _decodepoint(public_key)
        s = int.from_bytes(signature[32:], "little")
        h = int.from_bytes(
            hashlib.sha512(signature[:32] + public_key + message).digest(), "little"
        )
        return _scalarmult(_B, s) == _edwards_add(rr, _scalarmult(aa, h))
    except (ValueError, IndexError):
        return False


def verify_signature(pubkey_b58: str, message: bytes, signature: bytes) -> bool:
    """Verify a Solana wallet signature over `message`. pubkey is base58."""
    try:
        pk = b58decode(pubkey_b58)
    except ValueError:
        return False
    return len(pk) == 32 and ed25519_verify(pk, message, signature)


def is_valid_pubkey(pubkey_b58: str) -> bool:
    try:
        return len(b58decode(pubkey_b58)) == 32
    except ValueError:
        return False


# --- display + in-game identity ----------------------------------------------
def abbreviate(pubkey_b58: str, head: int = 4, tail: int = 4) -> str:
    """Wallet -> short display handle, e.g. 'EUr2…pump'."""
    if len(pubkey_b58) <= head + tail + 1:
        return pubkey_b58
    return f"{pubkey_b58[:head]}…{pubkey_b58[-tail:]}"


def derive_mc_name(pubkey_b58: str) -> str:
    """Deterministic Minecraft username for a wallet. MC names are 3-16 chars
    of [A-Za-z0-9_]; base58 is already a subset of [A-Za-z0-9], so we just
    take head_tail (e.g. 'EUr2Qn_pump')."""
    head = pubkey_b58[:6]
    tail = pubkey_b58[-4:]
    return f"{head}_{tail}"[:16]


# --- holdings -> rank tier ----------------------------------------------------
@dataclass(frozen=True, slots=True)
class Tier:
    key: str
    name: str
    min_pct: float  # minimum % of total supply to reach this tier


# Rank ladder by % of total supply, ordered ascending; the top tier caps at
# 5% (holding ≥5% is the highest rank). The floor (`holder`, 0.0) is the base
# tier everyone lands in — just showing up earns the base daily; tiers climb
# from there. Override per-deployment via env if the tokenomics change.
DEFAULT_TIERS: tuple[Tier, ...] = (
    Tier("holder", "Holder", 0.0),
    Tier("bronze", "Bronze", 0.1),
    Tier("silver", "Silver", 0.5),
    Tier("gold", "Gold", 1.0),
    Tier("diamond", "Diamond", 2.5),
    Tier("whale", "Whale", 5.0),
)


def tier_for(pct: float, tiers: tuple[Tier, ...] = DEFAULT_TIERS) -> Tier | None:
    """Highest tier whose threshold `pct` meets. Floored at the base tier
    (`holder`, 0.0), so even a zero-balance wallet gets it — entry is open and
    everyone earns the base reward. Returns None only if the ladder has no
    0.0-threshold tier (defensive; the default ladder always does)."""
    chosen: Tier | None = None
    for t in tiers:
        if pct >= t.min_pct:
            chosen = t
    return chosen


def next_tier(pct: float, tiers: tuple[Tier, ...] = DEFAULT_TIERS) -> Tier | None:
    """The next tier above the holder's current standing — the lowest tier whose
    threshold `pct` has NOT yet met. None at the top of the ladder. Lets the
    in-game `/tier` show "Gold at 1.00% — hold 0.58% more" from a single source
    of truth (the ladder lives here, not duplicated in the mod)."""
    for t in tiers:
        if t.min_pct > pct:
            return t
    return None
