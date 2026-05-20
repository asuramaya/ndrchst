"""Push a server's pilot artifacts + the public pages to Cloudflare R2.

This moves distribution off the residential box: once published, clients
fetch config/manifest/mod-index/modpack/pilot.zip and the play/landing
pages from Cloudflare's edge instead of the box's uplink. The box only
does the one-time outbound upload per change.

Two modes:
  - light (default): the small, frequently-changing pieces — mods index,
    per-server json, the substitution jars, and the rendered pages.
  - heavy: also the big blobs (modpack.zip, pilot.zip), which change
    rarely and are slow to push over the box's uplink.
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
            "name": s.name,
            "version": s.version,
            "port": s.port,
            "status": s.status.value,
            "cross_play": s.cross_play,
            "bedrock_bridge_port": s.bedrock_bridge_port,
            "pilot_url": f"/pilot/{s.id}/pilot.zip",
            "config_url": f"/pilot/{s.id}/config.json",
        }
        for s in java_servers
    ]


def publish_server(
    *,
    cfg: r2.R2Config,
    server,
    servers_root: Path,
    pilots_root: Path,
    java_servers,
    play_url: str = "/play",
    downloads_base: str = "",
    heavy: bool = False,
) -> dict:
    """Upload one server's artifacts + the public pages to R2. Returns a
    summary {uploaded, keys, skipped_missing}."""
    # Lazy import: web depends on runtime, so import the renderers here to
    # avoid an import-time cycle.
    from ..web.public_pages import render_landing, render_play

    sid = server.id
    pdir = pilots_root / sid
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
        put_file(pdir / "config.json", f"pilot/{sid}/config.json", "application/json", _NO_CACHE)
        put_file(pdir / "manifest.json", f"pilot/{sid}/manifest.json", "application/json", _NO_CACHE)

        # Mods index — the pilot fetches this at <sync>/mods/index.json.
        idx = sdir / "mods-index.json"
        if idx.exists():
            data = idx.read_bytes()
            put(f"pilot/{sid}/mods/index.json", data, "application/json", _NO_CACHE)
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
                    put_file(jar, f"pilot/{sid}/mods/{e['filename']}",
                             "application/java-archive", _IMMUTABLE)
        else:
            missing.append(str(idx))

        if heavy:
            put_file(pdir / "modpack.zip", f"pilot/{sid}/modpack.zip", "application/zip", _NO_CACHE)
            from .pilot import bundle_path
            bp = bundle_path(sid)
            if bp is not None:
                put_file(Path(bp), f"pilot/{sid}/pilot.zip", "application/zip", _NO_CACHE)

        # Public pages + a machine-readable server list (single source of
        # truth: the same renderers the box's public app uses).
        play_servers = _play_server_dicts(java_servers)
        put("index.html", render_landing(play_url=play_url).encode("utf-8"),
            "text/html; charset=utf-8", _NO_CACHE)
        put("play.html", render_play(play_servers, downloads_base=downloads_base).encode("utf-8"),
            "text/html; charset=utf-8", _NO_CACHE)
        put("servers.json", json.dumps(play_servers).encode("utf-8"),
            "application/json", _NO_CACHE)

    return {"uploaded": len(uploaded), "keys": uploaded, "skipped_missing": missing}
