"""Push a server's client artifacts + the static public pages to Cloudflare R2.

This moves distribution off the residential box: once published, clients
fetch config/manifest/mod-index/client.zip and the landing/play pages from
Cloudflare's edge instead of the box's uplink. The box only does the one-time
outbound upload per change.

The big modpack pack zip (~200MB of static overrides art) is NOT hosted by
us — the client pulls it straight from CurseForge's CDN (see
Lifecycle.modpack_cdn_url). That kept the box's residential uplink from
choking on a 200MB atomic PUT. Everything else (pages, per-server json, mods
index + substitution jars, and the small client.zip bundle) ships on every
publish — none of it is large now that the modpack lives on the CDN.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx

from . import r2

_NO_CACHE = "no-cache"
_IMMUTABLE = "public, max-age=31536000, immutable"


def _play_server_dicts(java_servers) -> list[dict]:
    return [
        {
            "id": s.id,
            "name": s.name,
            "version": s.version,
            "port": s.port,
            "status": s.status.value,
            "cross_play": s.cross_play,
            "bedrock_bridge_port": s.bedrock_bridge_port,
            "client_url": f"/client/{s.id}/client.zip",
            "config_url": f"/client/{s.id}/config.json",
        }
        for s in java_servers
    ]


def publish_server(
    *,
    cfg: r2.R2Config,
    server,
    servers_root: Path,
    clients_root: Path,
    java_servers,
    downloads_base: str = "",
) -> dict:
    """Upload one server's client artifacts + the static public pages to R2.
    Returns a summary {uploaded, keys, skipped_missing}."""
    # Lazy import: web depends on runtime, so import the renderers here to
    # avoid an import-time cycle.
    from ..web.public_pages import render_landing, render_play

    sid = server.id
    pdir = clients_root / sid
    sdir = servers_root / sid
    uploaded: list[str] = []
    missing: list[str] = []

    with httpx.Client(timeout=600.0) as client:
        def put(key: str, body: bytes, ct: str, cc: str) -> None:
            r2.put_object(cfg, key, body, content_type=ct, cache_control=cc, client=client)
            uploaded.append(key)

        def put_file(src: Path, key: str, ct: str, cc: str) -> None:
            if src.exists():
                put(key, src.read_bytes(), ct, cc)
            else:
                missing.append(str(src))

        # Per-server json (small, must stay fresh).
        put_file(pdir / "config.json", f"client/{sid}/config.json", "application/json", _NO_CACHE)
        put_file(pdir / "manifest.json", f"client/{sid}/manifest.json", "application/json", _NO_CACHE)

        # Mods index — the client fetches this at <sync>/mods/index.json.
        idx = sdir / "mods-index.json"
        if idx.exists():
            data = idx.read_bytes()
            put(f"client/{sid}/mods/index.json", data, "application/json", _NO_CACHE)
            # Substitution jars: index entries served from origin (not the
            # CDN). Small set; clients fetch these from the edge via the
            # index's origin_url, so they must exist in R2.
            try:
                entries = json.loads(data).get("mods", [])
            except json.JSONDecodeError:
                entries = []
            for e in entries:
                if e.get("from_cdn") is False and (e.get("target") or "mods") == "mods":
                    jar = sdir / "mods" / e["filename"]
                    put_file(jar, f"client/{sid}/mods/{e['filename']}",
                             "application/java-archive", _IMMUTABLE)
        else:
            missing.append(str(idx))

        # Client bundle — the launcher source zip the "Download client" button
        # serves. Small now that the modpack comes from CurseForge's CDN (the
        # ~200MB modpack.zip is intentionally never pushed), so it ships on
        # every publish rather than being gated behind a "heavy" flag.
        from .client import bundle_path
        bp = bundle_path(sid)
        if bp is not None:
            put_file(Path(bp), f"client/{sid}/client.zip", "application/zip", _NO_CACHE)
        else:
            missing.append(f"{pdir}/client.zip")

        # End-themed game assets (icons, decor, favicon, banner) → R2 under
        # game/, the same keys the box serves at /game and the pages reference.
        game_dir = Path(__file__).resolve().parent.parent / "web" / "static" / "game"
        _ct = {".png": "image/png", ".gif": "image/gif"}
        for f in sorted(game_dir.rglob("*")):
            if f.is_file():
                key = "game/" + f.relative_to(game_dir).as_posix()
                put(key, f.read_bytes(), _ct.get(f.suffix, "application/octet-stream"), _IMMUTABLE)

        # Machine-readable server list — clients enumerate available servers
        # and their per-server client/config URLs from this.
        play_servers = _play_server_dicts(java_servers)
        put("servers.json", json.dumps(play_servers).encode("utf-8"),
            "application/json", _NO_CACHE)

        # Static public pages (single source of truth: the same renderers the
        # edge serves at / and /play).
        put("index.html", render_landing(downloads_base=downloads_base).encode("utf-8"),
            "text/html; charset=utf-8", _NO_CACHE)
        put("play.html", render_play(play_servers, downloads_base=downloads_base).encode("utf-8"),
            "text/html; charset=utf-8", _NO_CACHE)

    return {"uploaded": len(uploaded), "keys": uploaded, "skipped_missing": missing}
