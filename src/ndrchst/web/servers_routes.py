"""Servers feature — HTML routes.

The same URL returns either a full page or an htmx fragment depending on the
HX-Request header. Mutating routes (create, delete, start, stop) always
return the updated card or list partial.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from ..api.deps import db, require_lifecycle, state
from ..platforms import REGISTRY as PLATFORMS
from ..runtime.lifecycle import CreateRequest, Lifecycle, LifecycleError
from ..store import servers as srv_store

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))

router = APIRouter()


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


@router.get("/", response_class=HTMLResponse)
def index(request: Request, conn: sqlite3.Connection = Depends(db)) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request,
        "servers/list.html",
        {
            "active": "servers",
            "servers": srv_store.list_all(conn),
            "docker_error": state(request).docker_error,
            "docker_available": state(request).lifecycle is not None,
        },
    )


@router.get("/servers/new", response_class=HTMLResponse)
def new_form(request: Request) -> HTMLResponse:
    """Returns the create-form panel. Loaded into #overlay-slot via htmx."""
    return TEMPLATES.TemplateResponse(
        request,
        "servers/_create_form.html",
        {"platforms": list(PLATFORMS.values()), "error": None},
    )


@router.post("/servers", response_class=HTMLResponse)
async def create(
    request: Request,
    name: str = Form(...),
    platform_id: str = Form(...),
    version: str = Form(...),
    port: int = Form(...),
    memory_mb: int = Form(2048),
    cross_play: bool = Form(False),
    lifecycle: Lifecycle = Depends(require_lifecycle),
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    try:
        await lifecycle.create(CreateRequest(
            name=name, platform_id=platform_id, version=version,
            port=port, memory_mb=memory_mb, cross_play=cross_play,
        ))
    except LifecycleError as e:
        # Re-render the form with the error inline; status 422 so htmx knows it failed
        return TEMPLATES.TemplateResponse(
            request,
            "servers/_create_form.html",
            {"platforms": list(PLATFORMS.values()), "error": str(e)},
            status_code=422,
        )

    # Success: close the overlay and refresh the list. Two htmx tricks:
    #   - HX-Trigger fires a client event so the list re-fetches itself
    #   - HX-Reswap=none + empty body keeps the swap target clean
    response = HTMLResponse(content="")
    response.headers["HX-Trigger"] = "ndrchst:servers-changed"
    response.headers["HX-Reswap"] = "innerHTML"
    response.headers["HX-Retarget"] = "#overlay-slot"
    return response


@router.get("/servers/list", response_class=HTMLResponse)
def list_fragment(
    request: Request, conn: sqlite3.Connection = Depends(db)
) -> HTMLResponse:
    """Fragment used by HX-Trigger='ndrchst:servers-changed' to refresh."""
    return TEMPLATES.TemplateResponse(
        request, "servers/_grid.html", {"servers": srv_store.list_all(conn)}
    )


@router.delete("/servers/{server_id}", response_class=HTMLResponse)
async def delete(
    request: Request,
    server_id: str,
    lifecycle: Lifecycle = Depends(require_lifecycle),
) -> Response:
    try:
        await lifecycle.delete(server_id, remove_files=False)
    except LifecycleError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    # htmx swaps the card out by replacing it with empty content
    return HTMLResponse(content="", status_code=status.HTTP_200_OK)


@router.post("/servers/{server_id}/start", response_class=HTMLResponse)
async def start(
    request: Request,
    server_id: str,
    lifecycle: Lifecycle = Depends(require_lifecycle),
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    try:
        await lifecycle.start(server_id)
    except LifecycleError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _render_card(request, conn, server_id)


@router.post("/servers/{server_id}/stop", response_class=HTMLResponse)
async def stop(
    request: Request,
    server_id: str,
    lifecycle: Lifecycle = Depends(require_lifecycle),
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    try:
        await lifecycle.stop(server_id)
    except LifecycleError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _render_card(request, conn, server_id)


def _render_card(
    request: Request, conn: sqlite3.Connection, server_id: str
) -> HTMLResponse:
    server = srv_store.get(conn, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="server not found")
    return TEMPLATES.TemplateResponse(
        request, "servers/_card.html", {"server": server}
    )
