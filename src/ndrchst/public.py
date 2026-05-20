"""Public ndrchst surface — for end users, not admins.

Boots as a second FastAPI app on a different port (env NDRCHST_PUBLIC_PORT,
default 8081). Read-only: lists running servers, serves per-server pilot
bundles. No admin routes, no mutation, no Docker access. Safe to expose
publicly (behind Cloudflare or similar).

Distinct from the admin surface (:8080) so:
  - We can put Cloudflare Access on admin and leave the public open.
  - A misrouted request to the public app can't trigger /api/servers POST.
"""
from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .domain.models import Family
from .logging_setup import configure as configure_logging
from .runtime.pilot import bundle_path as pilot_bundle_path
from .store import servers as srv_store
from .store.db import DEFAULT_DB_PATH, connect
from .web.public_pages import render_landing, render_play


def create_public_app(*, db_path: Path | None = None) -> FastAPI:
    """Factory. The public app keeps its own SQLite connection (read-only)."""
    conn_holder: dict[str, sqlite3.Connection] = {}

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        conn_holder["conn"] = connect(db_path or DEFAULT_DB_PATH)
        try:
            yield
        finally:
            conn_holder["conn"].close()
            conn_holder.clear()

    app = FastAPI(
        title="ndrchst public", version="0.0.1",
        docs_url=None, redoc_url=None,
        lifespan=lifespan,
    )

    def _conn() -> sqlite3.Connection:
        return conn_holder["conn"]

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "surface": "public"}

    def _play_servers() -> list[dict]:
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
            for s in srv_store.list_all(_conn())
            if s.family is Family.JAVA
        ]

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        # Domain-aware root: play.<domain> IS the play page; www / anything
        # else gets the marketing landing. One app serves both hosts behind
        # Cloudflare so each domain's root is "proper".
        import os
        host = (request.headers.get("host") or "").split(":")[0].lower()
        if host.startswith("play."):
            downloads_base = os.environ.get("NDRCHST_PILOT_DOWNLOADS_BASE", "")
            return HTMLResponse(render_play(_play_servers(), downloads_base=downloads_base))
        play_url = os.environ.get("NDRCHST_PLAY_URL", "/play")
        return HTMLResponse(render_landing(play_url=play_url))

    @app.get("/play", response_class=HTMLResponse)
    def play() -> HTMLResponse:
        import os
        downloads_base = os.environ.get("NDRCHST_PILOT_DOWNLOADS_BASE", "")
        return HTMLResponse(render_play(_play_servers(), downloads_base=downloads_base))

    @app.get("/servers")
    def list_servers() -> list[dict]:
        out = []
        for s in srv_store.list_all(_conn()):
            if s.family is not Family.JAVA:
                continue
            out.append({
                "id": s.id,
                "name": s.name,
                "mc_version": s.version,
                "port": s.port,
                "cross_play": s.cross_play,
                "bedrock_bridge_port": s.bedrock_bridge_port,
                "status": s.status.value,
                "pilot_url": f"/pilot/{s.id}/pilot.zip",
                "config_url": f"/pilot/{s.id}/config.json",
            })
        return out

    # Pilot bundles regenerate on demand (POST /servers/{id}/pilot/regenerate
    # on the admin plane). If a CDN ahead of us caches the old copy, the new
    # config sits unreachable until TTL expiry. `no-store` opts every layer
    # — browsers + Cloudflare — out of caching these per-server files.
    _NO_STORE = {"Cache-Control": "no-store, no-cache, must-revalidate"}

    @app.get("/pilot/{server_id}/pilot.zip")
    def download_pilot(server_id: str) -> FileResponse:
        server = srv_store.get(_conn(), server_id)
        if server is None:
            raise HTTPException(status_code=404, detail="server not found")
        path = pilot_bundle_path(server_id)
        if path is None:
            raise HTTPException(
                status_code=404,
                detail="pilot bundle not built yet (Java servers only); recreate the server",
            )
        return FileResponse(
            path,
            media_type="application/zip",
            filename=f"ndrchst-pilot-{server.name.replace(' ', '_')}.zip",
            headers=_NO_STORE,
        )

    @app.get("/pilot/{server_id}/config.json")
    def pilot_config(server_id: str) -> JSONResponse:
        from .runtime.pilot import PILOTS_ROOT_DEFAULT
        cfg_path = PILOTS_ROOT_DEFAULT / server_id / "config.json"
        if not cfg_path.exists():
            raise HTTPException(status_code=404, detail="config not found")
        import json
        return JSONResponse(json.loads(cfg_path.read_text()), headers=_NO_STORE)

    @app.get("/pilot/{server_id}/manifest.json")
    def pilot_manifest(server_id: str) -> JSONResponse:
        from .runtime.pilot import PILOTS_ROOT_DEFAULT
        mp = PILOTS_ROOT_DEFAULT / server_id / "manifest.json"
        if not mp.exists():
            raise HTTPException(status_code=404, detail="manifest not found")
        import json
        return JSONResponse(json.loads(mp.read_text()), headers=_NO_STORE)

    @app.get("/pilot/{server_id}/modpack.zip")
    def pilot_modpack(server_id: str) -> FileResponse:
        """Per-server modpack zip companion to pilot.zip — staged when
        the operator wants the pilot to install a CF client pack that CF's
        own CDN won't serve directly (which is most client packs).

        Used for the overrides/* tree (configs, kubejs, defaultconfigs);
        the actual mod jars come from the /mods/ endpoints below so the
        client mirrors the server's curated set, not whatever the upstream
        CF manifest happens to point at."""
        from .runtime.pilot import PILOTS_ROOT_DEFAULT
        path = PILOTS_ROOT_DEFAULT / server_id / "modpack.zip"
        if not path.exists():
            raise HTTPException(status_code=404, detail="modpack not staged for this server")
        return FileResponse(
            path,
            media_type="application/zip",
            filename="modpack.zip",
            headers=_NO_STORE,
        )

    @app.get("/pilot/{server_id}/mods/index.json")
    def pilot_mods_index(server_id: str) -> JSONResponse:
        """The mod set the pilot should mirror. Prefers the cached
        mods-index.json built by the admin (carries per-mod CDN download
        URLs so clients pull from CurseForge's global CDN, not the
        operator's uplink). Falls back to a live filename+sha1 listing
        with origin-only URLs if the index hasn't been built yet."""
        import hashlib
        import json
        import urllib.parse

        from .runtime.lifecycle import SERVERS_ROOT_DEFAULT
        server = srv_store.get(_conn(), server_id)
        if server is None:
            raise HTTPException(status_code=404, detail="server not found")

        cached = SERVERS_ROOT_DEFAULT / server_id / "mods-index.json"
        if cached.exists():
            return JSONResponse(json.loads(cached.read_text()), headers=_NO_STORE)

        # Fallback: live listing, all served from origin (no CDN URLs).
        mods_dir = SERVERS_ROOT_DEFAULT / server_id / "mods"
        if not mods_dir.exists():
            return JSONResponse({"server_id": server_id, "mods": []}, headers=_NO_STORE)
        out = []
        for p in sorted(mods_dir.iterdir()):
            if not p.is_file() or not p.name.endswith(".jar"):
                continue
            h = hashlib.sha1()
            with p.open("rb") as f:
                for chunk in iter(lambda: f.read(64 * 1024), b""):
                    h.update(chunk)
            origin = f"/pilot/{server_id}/mods/{urllib.parse.quote(p.name, safe='')}"
            out.append({
                "filename": p.name,
                "size": p.stat().st_size,
                "sha1": h.hexdigest(),
                "url": origin,
                "origin_url": origin,
                "from_cdn": False,
                "target": "mods",
            })
        return JSONResponse({"server_id": server_id, "mods": out}, headers=_NO_STORE)

    @app.get("/pilot/{server_id}/mods/{filename}")
    def pilot_mod_file(server_id: str, filename: str) -> FileResponse:
        """Stream a single mod jar — used for incremental sync (a handful
        of changed mods). For first install, prefer mods.zip (one request
        instead of ~450, which the tunnel serves orders of magnitude faster)."""
        from .runtime.lifecycle import SERVERS_ROOT_DEFAULT
        # Path-traversal guard: filename must be a plain *.jar with no separators.
        if "/" in filename or "\\" in filename or filename.startswith("."):
            raise HTTPException(status_code=400, detail="unsafe filename")
        if not filename.endswith(".jar"):
            raise HTTPException(status_code=400, detail="not a jar")
        path = SERVERS_ROOT_DEFAULT / server_id / "mods" / filename
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="mod not found")
        return FileResponse(
            path,
            media_type="application/java-archive",
            filename=filename,
            headers=_NO_STORE,
        )

    @app.get("/pilot/{server_id}/mods.zip")
    def pilot_mods_bundle(server_id: str) -> FileResponse:
        """Bundle the server's entire mods/ set into one zip. Bulk first-
        install path: pulling ~450 jars as individual requests through the
        tunnel is dominated by per-request overhead (~25s/file observed),
        but one large stream runs at full bandwidth. The zip is rebuilt
        only when the mods dir changes (mtime check) and cached next to
        the data dir."""
        import zipfile

        from .runtime.lifecycle import SERVERS_ROOT_DEFAULT
        server = srv_store.get(_conn(), server_id)
        if server is None:
            raise HTTPException(status_code=404, detail="server not found")
        mods_dir = SERVERS_ROOT_DEFAULT / server_id / "mods"
        if not mods_dir.exists():
            raise HTTPException(status_code=404, detail="no mods for this server")
        jars = sorted(p for p in mods_dir.iterdir()
                      if p.is_file() and p.name.endswith(".jar"))
        cache = mods_dir.parent / "mods-bundle.zip"
        # Rebuild if the cache is missing or older than the newest jar.
        newest = max((p.stat().st_mtime for p in jars), default=0)
        if not cache.exists() or cache.stat().st_mtime < newest:
            tmp = cache.with_suffix(".zip.building")
            with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED) as zf:
                for p in jars:
                    zf.write(p, arcname=p.name)
            tmp.replace(cache)
        return FileResponse(
            cache,
            media_type="application/zip",
            filename="mods.zip",
            headers=_NO_STORE,
        )

    return app


app = create_public_app()
