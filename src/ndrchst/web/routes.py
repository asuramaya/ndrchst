"""HTML routes (htmx-driven UI).

Each feature owns a route module under web/. v0 only has servers; everything
else is a "coming soon" placeholder page so the sidebar nav doesn't dead-end.

Routes here distinguish full-page vs htmx-fragment responses by checking the
HX-Request header. The same URL serves both, simplifying refresh/back-button
behaviour.
"""
from __future__ import annotations

import os
import sqlite3
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .. import __version__
from ..api.deps import db, state
from ..domain import sysinfo
from ..domain.models import Family, ServerStatus
from ..runtime.lifecycle import SERVERS_ROOT_DEFAULT
from ..runtime.pilot import PILOTS_ROOT_DEFAULT
from ..runtime.pilot import bundle_path as pilot_bundle_path
from ..store import servers as srv_store
from ..store.db import DEFAULT_DB_PATH
from .detail_routes import router as detail_router
from .servers_routes import router as servers_router

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
# Expose byte/duration formatters to templates.
TEMPLATES.env.globals["human_bytes"] = sysinfo.human_bytes
TEMPLATES.env.globals["human_duration"] = sysinfo.human_duration

router = APIRouter()
router.include_router(servers_router)
router.include_router(detail_router)


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


@router.get("/assets", response_class=HTMLResponse)
def assets_page(
    request: Request,
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    """Read-only global view of every installed asset across every server."""
    rows = conn.execute(
        """SELECT a.server_id, a.source_id, a.asset_id, a.kind, a.version,
                  a.installed_at, s.name AS server_name, s.family
             FROM installed_assets a
             JOIN servers s ON s.id = a.server_id
             ORDER BY a.installed_at DESC""",
    ).fetchall()
    by_server: dict[str, dict] = defaultdict(lambda: {"name": "", "family": "", "assets": []})
    for r in rows:
        bucket = by_server[r["server_id"]]
        bucket["name"] = r["server_name"]
        bucket["family"] = r["family"]
        bucket["server_id"] = r["server_id"]
        bucket["assets"].append({
            "source_id": r["source_id"],
            "asset_id": r["asset_id"],
            "kind": r["kind"],
            "version": r["version"],
            "installed_at": r["installed_at"],
        })
    # Surface servers with zero installed assets too, so users see them
    # explicitly empty rather than missing.
    for s in srv_store.list_all(conn):
        if s.id not in by_server:
            by_server[s.id] = {
                "name": s.name, "family": s.family.value,
                "server_id": s.id, "assets": [],
            }
    grouped = sorted(by_server.values(), key=lambda x: x["name"].lower())
    total = sum(len(g["assets"]) for g in grouped)
    return TEMPLATES.TemplateResponse(
        request,
        "assets.html",
        {
            "active": "assets",
            "grouped": grouped,
            "total": total,
            "docker_error": state(request).docker_error,
        },
    )


@router.get("/system", response_class=HTMLResponse)
async def system_page(request: Request) -> HTMLResponse:
    """Host metrics + Docker engine info + a roll-up of managed servers."""
    s = state(request)
    host = sysinfo.host_metrics()
    disks = {
        "servers root": sysinfo.disk_usage(SERVERS_ROOT_DEFAULT),
        "pilots root": sysinfo.disk_usage(PILOTS_ROOT_DEFAULT),
    }
    engine = await s.lifecycle.engine_info() if s.lifecycle is not None else None

    servers = srv_store.list_all(s.conn)
    counts = {
        "total": len(servers),
        "running": sum(1 for x in servers if x.status is ServerStatus.RUNNING),
        "java": sum(1 for x in servers if x.family is Family.JAVA),
        "bedrock": sum(1 for x in servers if x.family is Family.BEDROCK),
    }
    return TEMPLATES.TemplateResponse(
        request,
        "system.html",
        {
            "active": "system",
            "host": host,
            "disks": disks,
            "engine": engine,
            "counts": counts,
            "docker_error": s.docker_error,
        },
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    """Effective runtime configuration (env-driven) + per-server pilot
    wiring status. Read-only: these are set via environment / systemd at
    boot, surfaced here so an operator can verify the public-edge setup
    without SSHing in."""
    s = state(request)
    config = [
        ("Edge URL", os.environ.get("NDRCHST_EDGE_URL", ""),
         "NDRCHST_EDGE_URL", "Public HTTPS base where pilots fetch mods/config."),
        ("Public host", os.environ.get("NDRCHST_PUBLIC_HOST", ""),
         "NDRCHST_PUBLIC_HOST", "Address Minecraft clients dial (when not tunnelled)."),
        ("Tunnel hostname", os.environ.get("NDRCHST_TUNNEL_HOSTNAME", ""),
         "NDRCHST_TUNNEL_HOSTNAME", "Cloudflare hostname the pilot tunnels through."),
        ("Public surface port", os.environ.get("NDRCHST_PUBLIC_PORT", "8081"),
         "NDRCHST_PUBLIC_PORT", "Port the read-only public app binds."),
        ("Servers root", str(SERVERS_ROOT_DEFAULT), "", "Per-server data dirs."),
        ("Pilots root", str(PILOTS_ROOT_DEFAULT), "", "Staged pilot bundles + modpacks."),
        ("Database", str(DEFAULT_DB_PATH), "", "SQLite control-plane state."),
        ("Version", __version__, "", "Running ndrchst version."),
    ]
    pilots = []
    for srv in srv_store.list_all(s.conn):
        if srv.family is not Family.JAVA:
            continue
        pdir = PILOTS_ROOT_DEFAULT / srv.id
        sdir = SERVERS_ROOT_DEFAULT / srv.id
        pilots.append({
            "name": srv.name,
            "id": srv.id,
            "bundle": pilot_bundle_path(srv.id) is not None,
            "config": (pdir / "config.json").exists(),
            "modpack": (pdir / "modpack.zip").exists(),
            "mods_index": (sdir / "mods-index.json").exists(),
        })
    return TEMPLATES.TemplateResponse(
        request,
        "settings.html",
        {
            "active": "settings",
            "config": config,
            "pilots": pilots,
            "docker_error": s.docker_error,
        },
    )
