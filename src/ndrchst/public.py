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

from .domain.models import Family, ServerStatus
from .logging_setup import configure as configure_logging
from .runtime.pilot import bundle_path as pilot_bundle_path
from .store import servers as srv_store
from .store.db import DEFAULT_DB_PATH, connect


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

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        servers = [s for s in srv_store.list_all(_conn()) if s.family is Family.JAVA]
        # Status filter: ignore deleted-but-not-cleaned-up rows
        servers = [s for s in servers if s.status is not ServerStatus.CRASHED]
        body = _render_index(request, servers)
        return HTMLResponse(body)

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
        """List every .jar in the server's data_dir/mods/ with its size +
        SHA1. The pilot uses this to sync its local mods/ to whatever the
        server currently has — operator's substitutions (e.g. CC: Tweaked
        version bumps when upstream manifest rots) propagate automatically.
        Disabled jars (*.jar.disabled) are excluded."""
        import hashlib

        from .runtime.lifecycle import SERVERS_ROOT_DEFAULT
        server = srv_store.get(_conn(), server_id)
        if server is None:
            raise HTTPException(status_code=404, detail="server not found")
        mods_dir = SERVERS_ROOT_DEFAULT / server_id / "mods"
        if not mods_dir.exists():
            return JSONResponse({"mods": []}, headers=_NO_STORE)
        out = []
        for p in sorted(mods_dir.iterdir()):
            if not p.is_file() or not p.name.endswith(".jar"):
                continue
            h = hashlib.sha1()
            with p.open("rb") as f:
                for chunk in iter(lambda: f.read(64 * 1024), b""):
                    h.update(chunk)
            out.append({
                "filename": p.name,
                "size": p.stat().st_size,
                "sha1": h.hexdigest(),
            })
        return JSONResponse({"server_id": server_id, "mods": out}, headers=_NO_STORE)

    @app.get("/pilot/{server_id}/mods/{filename}")
    def pilot_mod_file(server_id: str, filename: str) -> FileResponse:
        """Stream a single mod jar from the server's mods directory."""
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

    return app


def _render_index(request: Request, servers: list) -> str:
    """Tiny inline HTML — no template engine for the public surface (keeps
    it self-contained, no template path discovery on a different bind).
    Server list links to the matching pilot download.
    """
    rows = []
    if not servers:
        rows.append('<p class="empty">No servers available yet.</p>')
    for s in servers:
        cross_play_badge = ' <span class="badge">cross-play</span>' if s.cross_play else ""
        rows.append(
            f'<div class="row">'
            f'  <div class="name">{s.name}{cross_play_badge}</div>'
            f'  <div class="meta">Minecraft {s.version} · port {s.port}'
            + (f' · bedrock {s.bedrock_bridge_port}/udp' if s.cross_play else "")
            + '</div>'
            f'  <div class="actions">'
            f'    <a href="/pilot/{s.id}/pilot.zip" class="btn">Download Pilot</a>'
            f'    <a href="/pilot/{s.id}/config.json" class="btn-ghost">config</a>'
            f'  </div>'
            f'</div>'
        )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>ndrchst — join a server</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            background: #0f1115; color: #e4e6eb; max-width: 720px; margin: 2rem auto; padding: 0 1rem; }}
    h1 {{ font-weight: 700; letter-spacing: -0.02em; }}
    .row {{ background: #1a1d24; padding: 1rem 1.2rem; border-radius: 8px; margin-bottom: 0.8rem;
           display: grid; grid-template-columns: 1fr auto; gap: 0.4rem 1rem; align-items: center; }}
    .name {{ font-weight: 600; font-size: 1.1rem; grid-column: 1; }}
    .meta {{ color: #97a0b0; font-size: 0.85rem; grid-column: 1; font-family: monospace; }}
    .actions {{ grid-row: 1 / 3; grid-column: 2; }}
    .badge {{ display: inline-block; padding: 0.1rem 0.45rem; font-size: 0.7rem;
             background: #2a2f3a; color: #c4cad6; border-radius: 4px; vertical-align: middle; }}
    .btn, .btn-ghost {{ display: inline-block; padding: 0.4rem 0.8rem; border-radius: 6px;
                       text-decoration: none; font-size: 0.85rem; }}
    .btn {{ background: #4d7cfe; color: white; }}
    .btn-ghost {{ color: #97a0b0; }}
    .btn:hover {{ background: #5a89ff; }}
    .empty {{ color: #97a0b0; }}
    footer {{ margin-top: 3rem; color: #4a5260; font-size: 0.8rem; }}
    code {{ background: #1a1d24; padding: 0.1rem 0.3rem; border-radius: 3px; }}
  </style>
</head>
<body>
  <h1>ndrchst</h1>
  <p>Private Minecraft servers. Download the matching pilot client below.</p>
  {''.join(rows)}
  <footer>
    Pilot is a small offline Minecraft launcher pinned to each server.
    After downloading: <code>unzip pilot.zip &amp;&amp; cd pilot &amp;&amp; ./launch.sh</code>
  </footer>
</body>
</html>"""


app = create_public_app()
