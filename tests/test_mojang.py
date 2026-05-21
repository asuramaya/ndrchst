"""Mojang skin-lookup tests — username→texture-hash resolution + texture fetch.

Network is mocked with httpx MockTransport, so these run offline and pin the
two-step Mojang flow (profile UUID → base64 textures property → SKIN hash) plus
the validation that keeps the import path SSRF-free (hash-only) and PNG-only.
"""
from __future__ import annotations

import base64
import json

import httpx

from ndrchst.runtime import mojang


def test_lookup_skin_resolves_username_to_texture_hash():
    tex = "b" * 64
    val = base64.b64encode(json.dumps({
        "textures": {"SKIN": {
            "url": f"http://textures.minecraft.net/texture/{tex}",
            "metadata": {"model": "slim"}}}}).encode()).decode()

    def handler(req: httpx.Request) -> httpx.Response:
        u = str(req.url)
        if u.endswith("/users/profiles/minecraft/Notch"):
            return httpx.Response(200, json={"id": "069a79", "name": "Notch"})
        if "/session/minecraft/profile/069a79" in u:
            return httpx.Response(200, json={
                "properties": [{"name": "textures", "value": val}]})
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        assert mojang.lookup_skin("Notch", client=c) == {
            "name": "Notch", "uuid": "069a79", "texture": tex, "model": "slim"}


def test_lookup_skin_unknown_user_returns_none():
    with httpx.Client(transport=httpx.MockTransport(
            lambda req: httpx.Response(404))) as c:
        assert mojang.lookup_skin("Ghost", client=c) is None


def test_lookup_skin_rejects_bad_username_without_network():
    # Invalid chars / over 16 chars are rejected before any request fires.
    assert mojang.lookup_skin("not a name!") is None
    assert mojang.lookup_skin("x" * 17) is None
    assert mojang.lookup_skin("") is None


def test_fetch_texture_validates_hash_and_png():
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

    def handler(req: httpx.Request) -> httpx.Response:
        assert str(req.url) == "https://textures.minecraft.net/texture/" + ("c" * 64)
        return httpx.Response(200, content=png)

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        assert mojang.fetch_texture("c" * 64, client=c) == png
    # A non-hex / wrong-length hash never touches the network.
    assert mojang.fetch_texture("nothex") is None


def test_fetch_texture_rejects_non_png():
    with httpx.Client(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, content=b"<html>nope"))) as c:
        assert mojang.fetch_texture("d" * 64, client=c) is None
