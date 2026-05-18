"""HTML routes (htmx-driven UI).

Each feature owns a route module under web/. v0 only has servers; everything
else is a "coming soon" placeholder page so the sidebar nav doesn't dead-end.

Routes here distinguish full-page vs htmx-fragment responses by checking the
HX-Request header. The same URL serves both, simplifying refresh/back-button
behaviour.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..api.deps import db, state
from ..store import servers as srv_store
from .detail_routes import router as detail_router
from .servers_routes import router as servers_router

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))

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
def system_page(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request,
        "placeholder.html",
        {
            "active": "system",
            "title": "System",
            "body": "Host metrics and Docker engine info will live here.",
            "docker_error": state(request).docker_error,
        },
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request,
        "placeholder.html",
        {
            "active": "settings",
            "title": "Settings",
            "body": "Settings will land here once we have more than two.",
            "docker_error": state(request).docker_error,
        },
    )
