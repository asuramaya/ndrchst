"""Tests for the vendored wallet crypto. ed25519 verify is checked against
the official RFC 8032 §7.1 test vectors so we know the pure-Python impl is
correct without needing a third-party crypto library."""
from __future__ import annotations

import pytest

from ndrchst.domain.wallet import (
    DEFAULT_TIERS,
    abbreviate,
    b58decode,
    derive_mc_name,
    ed25519_verify,
    is_valid_pubkey,
    tier_for,
    verify_signature,
)

# RFC 8032 §7.1: (public_key_hex, message_hex, signature_hex)
_RFC8032 = [
    (
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555"
        "fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
    ),
    (
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da0"
        "85ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
    ),
    (
        "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
        "af82",
        "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac1"
        "8ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a",
    ),
]


@pytest.mark.parametrize(("pk", "msg", "sig"), _RFC8032)
def test_ed25519_rfc8032_vectors_pass(pk: str, msg: str, sig: str):
    assert ed25519_verify(bytes.fromhex(pk), bytes.fromhex(msg), bytes.fromhex(sig))


def test_ed25519_rejects_tampered_message():
    pk, _, sig = _RFC8032[1]
    assert not ed25519_verify(bytes.fromhex(pk), b"\x73", bytes.fromhex(sig))


def test_ed25519_rejects_tampered_signature():
    pk, msg, sig = _RFC8032[2]
    bad = bytearray(bytes.fromhex(sig))
    bad[0] ^= 0x01
    assert not ed25519_verify(bytes.fromhex(pk), bytes.fromhex(msg), bytes(bad))


def test_ed25519_rejects_wrong_lengths():
    assert not ed25519_verify(b"short", b"m", b"\x00" * 64)
    assert not ed25519_verify(b"\x00" * 32, b"m", b"\x00" * 10)


def test_b58decode_basics():
    assert b58decode("1") == b"\x00"
    assert b58decode("11") == b"\x00\x00"
    # A real Solana mint must decode to exactly 32 bytes.
    mint = "EUr2QnpmavMw51JiFYeTRnUywY7mPAtouzyY2P21pump"
    assert len(b58decode(mint)) == 32
    assert is_valid_pubkey(mint)


def test_b58decode_rejects_bad_chars():
    with pytest.raises(ValueError):
        b58decode("0OIl")  # none of these are in the base58 alphabet
    assert not is_valid_pubkey("not-a-key!")


def test_verify_signature_with_base58_pubkey():
    # base58-encode the RFC vector pubkey, then verify through the public API.
    pk_bytes = bytes.fromhex(_RFC8032[1][0])
    n = int.from_bytes(pk_bytes, "big")
    alph = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    enc = ""
    while n:
        n, r = divmod(n, 58)
        enc = alph[r] + enc
    enc = "1" * (len(pk_bytes) - len(pk_bytes.lstrip(b"\x00"))) + enc
    assert verify_signature(enc, bytes.fromhex("72"), bytes.fromhex(_RFC8032[1][2]))
    assert not verify_signature(enc, b"\x73", bytes.fromhex(_RFC8032[1][2]))


def test_abbreviate_and_mc_name():
    mint = "EUr2QnpmavMw51JiFYeTRnUywY7mPAtouzyY2P21pump"
    assert abbreviate(mint) == "EUr2…pump"
    name = derive_mc_name(mint)
    assert name == "EUr2Qn_pump"
    assert 3 <= len(name) <= 16
    assert all(c.isalnum() or c == "_" for c in name)


def test_tier_for():
    # Floored at the base tier: entry is open, so even 0 holdings earns the
    # base 'holder' daily; tiers climb from there.
    assert tier_for(0.0).key == "holder"
    assert tier_for(-1) is None             # negatives never occur (holdings >= 0)
    assert tier_for(0.05).key == "holder"   # >0 but below bronze
    assert tier_for(0.2).key == "bronze"    # >=0.1
    assert tier_for(0.7).key == "silver"    # >=0.5
    assert tier_for(1.5).key == "gold"      # >=1.0
    assert tier_for(3.0).key == "diamond"   # >=2.5
    assert tier_for(6.0).key == "whale"     # >=5.0 (top tier caps at 5%)
    # ladder is ascending and the first threshold is 0
    assert [t.min_pct for t in DEFAULT_TIERS] == sorted(t.min_pct for t in DEFAULT_TIERS)
