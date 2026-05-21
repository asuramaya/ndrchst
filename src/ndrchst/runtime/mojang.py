"""Minecraft skin lookup via Mojang's public API + open texture CDN.

Powers the play-page "find a skin" search. We deliberately resolve skins by
Minecraft *username* (Mojang) rather than scraping a skin gallery: the obvious
target, The Skindex (minecraftskins.com), sits behind a Cloudflare managed JS
challenge that a server can't pass without a headless browser. Mojang's
endpoints and `textures.minecraft.net` are open and official, so this path is
reliable and dependency-free.

Two steps, both read-only:
  username -> profile UUID            (api.mojang.com)
  UUID     -> textures property       (sessionserver.mojang.com, base64 JSON)
             -> SKIN url -> 64-hex texture hash on textures.minecraft.net

The 64-hex hash is the only thing we hand back to the browser, and the only
thing import accepts — so the preview proxy and the import fetch can ONLY ever
hit textures.minecraft.net/texture/<hash>. No open redirect, no SSRF.
"""
from __future__ import annotations

import base64
import json
import logging
import re

import httpx

_log = logging.getLogger("ndrchst.mojang")

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

_API = "https://api.mojang.com"
_SESSION = "https://sessionserver.mojang.com"
_TEXTURES = "https://textures.minecraft.net/texture/"
_TIMEOUT = httpx.Timeout(connect=6.0, read=10.0, write=10.0, pool=6.0)
_MAX_TEXTURE_BYTES = 256 * 1024  # a skin is a tiny 64x64 PNG; cap defensively


def is_valid_username(name: str) -> bool:
    return bool(_USERNAME_RE.match(name))


def is_texture_hash(h: str) -> bool:
    return bool(_HEX64_RE.match(h))


def lookup_skin(username: str, *, client: httpx.Client | None = None) -> dict | None:
    """Resolve a Minecraft username to {name, uuid, texture, model}, where
    `texture` is the 64-hex hash on textures.minecraft.net and `model` is
    'slim' or 'classic'. None if the user doesn't exist or any step fails."""
    if not is_valid_username(username):
        return None
    owns = client is None
    c = client or httpx.Client(timeout=_TIMEOUT)
    try:
        r = c.get(f"{_API}/users/profiles/minecraft/{username}")
        if r.status_code != 200 or not r.content:
            return None
        prof = r.json()
        uuid, name = prof.get("id"), prof.get("name", username)
        if not uuid:
            return None
        r2 = c.get(f"{_SESSION}/session/minecraft/profile/{uuid}")
        if r2.status_code != 200:
            return None
        props = r2.json().get("properties", [])
        textures = next((p for p in props if p.get("name") == "textures"), None)
        if not textures:
            return None
        decoded = json.loads(base64.b64decode(textures["value"]))
        skin = decoded.get("textures", {}).get("SKIN")
        if not skin or not skin.get("url"):
            return None
        tex_hash = skin["url"].rstrip("/").rsplit("/", 1)[-1]
        if not is_texture_hash(tex_hash):
            return None
        model = "slim" if skin.get("metadata", {}).get("model") == "slim" else "classic"
        return {"name": name, "uuid": uuid, "texture": tex_hash, "model": model}
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        _log.debug("mojang lookup failed for %r", username, exc_info=True)
        return None
    finally:
        if owns:
            c.close()


def fetch_texture(tex_hash: str, *, client: httpx.Client | None = None) -> bytes | None:
    """Fetch the raw skin PNG for a 64-hex texture hash from Mojang's open
    texture CDN. None on a bad hash, a non-200, a non-PNG, or oversize."""
    if not is_texture_hash(tex_hash):
        return None
    owns = client is None
    c = client or httpx.Client(timeout=_TIMEOUT)
    try:
        r = c.get(f"{_TEXTURES}{tex_hash}")
        if r.status_code != 200:
            return None
        data = r.content
        if len(data) > _MAX_TEXTURE_BYTES or data[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        return data
    except httpx.HTTPError:
        _log.debug("texture fetch failed for %s", tex_hash, exc_info=True)
        return None
    finally:
        if owns:
            c.close()
