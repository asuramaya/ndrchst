"""Server detail page + all per-tab routes.

Layout: GET /servers/{id}/<tab> returns either a full page (browser nav) or
the tab partial (htmx swap). Mutation endpoints return updated partials.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..api.deps import db, require_lifecycle, state
from ..domain import files as files_mod
from ..domain import players as players_mod
from ..domain import properties as props_mod
from ..domain import worlds as worlds_mod
from ..domain.models import Family, Server
from ..mods.base import AssetKind
from ..mods.modrinth import Modrinth
from ..runtime import backup as backup_mod
from ..runtime.installer import (
    install as install_asset,
)
from ..runtime.installer import (
    list_installed,
    record_install,
    remove_installed,
)
from ..runtime.lifecycle import SERVERS_ROOT_DEFAULT, Lifecycle
from ..runtime.rcon import RCON, RCONError
from ..store import servers as srv_store

log = logging.getLogger("ndrchst.detail")

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))

router = APIRouter()

_VALID_TABS = ("console", "properties", "players", "worlds", "files", "mods", "backups")


def _get_server(conn: sqlite3.Connection, server_id: str) -> Server:
    s = srv_store.get(conn, server_id)
    if s is None:
        raise HTTPException(status_code=404, detail="server not found")
    return s


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


async def _rcon_call(request: Request, server: Server, fn, *, fallback=None):
    """Connect to RCON on the host-side port and invoke fn(rcon).

    For v0, RCON connection details come from server.properties (rcon.port,
    rcon.password). If RCON is not enabled or the server isn't running, we
    return the fallback rather than 500ing the UI.
    """
    props = props_mod.read(_data_dir(request, server.id))
    rcon_port = int(props.get("rcon.port") or 25575)
    password = props.get("rcon.password") or ""
    enabled = props.get("enable-rcon", "false").lower() == "true"
    if not enabled or not password:
        return fallback
    try:
        async with RCON("127.0.0.1", rcon_port, password, timeout=3.0) as rcon:
            return await fn(rcon)
    except (RCONError, OSError, TimeoutError) as e:
        log.warning("RCON call failed for %s: %s", server.id, e)
        return fallback


def _data_dir(request: Request, server_id: str) -> Path:
    """Servers root may have been overridden in tests; fall back to default."""
    lc = state(request).lifecycle
    root = lc._root if lc else SERVERS_ROOT_DEFAULT  # type: ignore[union-attr]
    return root / server_id


# Detail page (catch-all `{tab}` route) is REGISTERED AT THE END of this file
# so the specific routes (/files, /properties, /mods, /backups, /players/*)
# match first. Browser hits to /servers/{sid}/files etc. flow through the
# specific routes which dispatch to _render_tab below for full-vs-partial.


def _render_tab(
    request: Request,
    server: Server,
    tab: str,
    partial: str,
    ctx: dict,
) -> HTMLResponse:
    """Returns the partial when HX-Request is set, full detail page otherwise."""
    if _is_htmx(request):
        return TEMPLATES.TemplateResponse(
            request, f"servers/tabs/{partial}", {"server": server, **ctx},
        )
    return TEMPLATES.TemplateResponse(
        request, "servers/detail.html", {
            "server": server, "tab": tab, "active": "servers",
            "docker_error": state(request).docker_error, **ctx,
        },
    )


async def _tab_context(
    request: Request, server: Server, tab: str, conn: sqlite3.Connection
) -> dict:
    if tab == "properties":
        return {"properties": props_mod.read(_data_dir(request, server.id))}
    if tab == "players":
        return {"players": []}  # filled live; initial render is empty
    if tab == "worlds":
        return _worlds_ctx(request, server)
    if tab == "files":
        return _files_ctx(request, server.id, "")
    if tab == "mods":
        return {"installed": list_installed(conn, server.id)}
    if tab == "backups":
        return {"backups": backup_mod.list_for(server.id)}
    return {}


# ─── Worlds ─────────────────────────────────────────────────────────────────


def _worlds_ctx(request: Request, server: Server) -> dict:
    if server.family is Family.BEDROCK:
        return {"world": None}
    data_dir = _data_dir(request, server.id)
    try:
        return {"world": worlds_mod.read(data_dir)}
    except worlds_mod.WorldError:
        return {"world": None}


@router.post("/servers/{server_id}/worlds/gamerules", response_class=HTMLResponse)
async def worlds_save_gamerules(
    request: Request,
    server_id: str,
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    server = _get_server(conn, server_id)
    if server.family is Family.BEDROCK:
        raise HTTPException(status_code=400, detail="Bedrock world editing not supported")
    form = await request.form()
    updates = {k: str(v) for k, v in form.items()}
    try:
        worlds_mod.write_game_rules(_data_dir(request, server_id), updates)
    except worlds_mod.WorldError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return HTMLResponse(content="Saved. Restart the server to apply.")


# ─── Properties ─────────────────────────────────────────────────────────────


@router.post("/servers/{server_id}/properties", response_class=HTMLResponse)
async def save_properties(
    request: Request,
    server_id: str,
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    _get_server(conn, server_id)
    form = await request.form()
    updates = {k: str(v) for k, v in form.items()}
    props_mod.write(_data_dir(request, server_id), updates)
    return HTMLResponse(content="Saved. Restart the server to apply.")


# ─── Players (Java only) ────────────────────────────────────────────────────


@router.get("/servers/{server_id}/players/refresh", response_class=HTMLResponse)
async def players_refresh(
    request: Request,
    server_id: str,
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    server = _get_server(conn, server_id)
    if server.family is not Family.JAVA:
        raise HTTPException(status_code=400, detail="players UI is Java-only")
    players = await _rcon_call(request, server, players_mod.online, fallback=[])
    return TEMPLATES.TemplateResponse(
        request, "servers/tabs/_players_list.html",
        {"server": server, "players": players},
    )


@router.post("/servers/{server_id}/players/kick", response_class=HTMLResponse)
async def players_kick(
    request: Request,
    server_id: str,
    player: str = Form(...),
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    server = _get_server(conn, server_id)
    if server.family is not Family.JAVA:
        raise HTTPException(status_code=400, detail="players UI is Java-only")
    await _rcon_call(request, server, lambda r: players_mod.kick(r, player))
    players = await _rcon_call(request, server, players_mod.online, fallback=[])
    return TEMPLATES.TemplateResponse(
        request, "servers/tabs/_players_list.html",
        {"server": server, "players": players},
    )


@router.post("/servers/{server_id}/players/whitelist", response_class=HTMLResponse)
async def players_whitelist(
    request: Request,
    server_id: str,
    player: str = Form(...),
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    server = _get_server(conn, server_id)
    result = await _rcon_call(request, server, lambda r: players_mod.whitelist_add(r, player))
    return HTMLResponse(content=result or f"Added {player}")


@router.post("/servers/{server_id}/players/op", response_class=HTMLResponse)
async def players_op(
    request: Request,
    server_id: str,
    player: str = Form(...),
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    server = _get_server(conn, server_id)
    result = await _rcon_call(request, server, lambda r: players_mod.op(r, player))
    return HTMLResponse(content=result or f"Op granted to {player}")


# ─── Files ──────────────────────────────────────────────────────────────────


def _files_ctx(request: Request, server_id: str, path: str) -> dict:
    data_dir = _data_dir(request, server_id)
    entries = files_mod.list_dir(data_dir, path) if data_dir.exists() else []
    crumbs: list[tuple[str, str]] = []
    accum = ""
    for part in path.split("/") if path else []:
        accum = f"{accum}/{part}" if accum else part
        crumbs.append((part, accum))
    parent = "/".join(path.split("/")[:-1]) if path else ""
    return {"entries": entries, "path": path, "parent": parent, "crumbs": crumbs}


@router.get("/servers/{server_id}/files", response_class=HTMLResponse)
async def files_list(
    request: Request,
    server_id: str,
    path: str = "",
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    server = _get_server(conn, server_id)
    try:
        ctx = _files_ctx(request, server_id, path)
    except files_mod.PathError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _render_tab(request, server, "files", "_files_list.html", ctx)


@router.get("/servers/{server_id}/files/edit", response_class=HTMLResponse)
async def files_edit_form(
    request: Request,
    server_id: str,
    path: str,
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    server = _get_server(conn, server_id)
    try:
        content = files_mod.read_text(_data_dir(request, server_id), path)
    except files_mod.PathError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    parent = "/".join(path.split("/")[:-1]) if path else ""
    return TEMPLATES.TemplateResponse(
        request, "servers/tabs/_files_edit.html",
        {"server": server, "path": path, "parent": parent, "content": content},
    )


@router.post("/servers/{server_id}/files/edit", response_class=HTMLResponse)
async def files_edit_save(
    request: Request,
    server_id: str,
    path: str,
    content: str = Form(""),
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    _get_server(conn, server_id)  # 404 guard
    try:
        files_mod.write_text(_data_dir(request, server_id), path, content)
    except files_mod.PathError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    parent = "/".join(path.split("/")[:-1]) if path else ""
    return await files_list(request, server_id, parent, conn=conn)


# ─── Mods (Marketplace) ────────────────────────────────────────────────────


@router.get("/servers/{server_id}/mods/search", response_class=HTMLResponse)
async def mods_search(
    request: Request,
    server_id: str,
    q: str,
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    server = _get_server(conn, server_id)
    source = state(request).modrinth or Modrinth()
    # For Bedrock, default the kind to RESOURCEPACK; Java -> MOD (good default)
    kind = AssetKind.RESOURCEPACK if server.family is Family.BEDROCK else AssetKind.MOD
    results = await source.search(q, kind=kind, loader=None, game_version=None, limit=20)
    return TEMPLATES.TemplateResponse(
        request, "servers/tabs/_mods_results.html",
        {"server": server, "results": results},
    )


@router.post("/servers/{server_id}/mods/install", response_class=HTMLResponse)
async def mods_install(
    request: Request,
    server_id: str,
    source_id: str = Form(...),
    asset_id: str = Form(...),
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    server = _get_server(conn, server_id)
    if source_id != "modrinth":
        raise HTTPException(status_code=400, detail=f"unknown source: {source_id}")
    source = state(request).modrinth or Modrinth()
    versions = await source.versions(asset_id, loader=None, game_version=server.version)
    if not versions:
        # Try without game_version filter as a fallback
        versions = await source.versions(asset_id)
    if not versions:
        raise HTTPException(status_code=400, detail="no compatible version found")
    kind = AssetKind.RESOURCEPACK if server.family is Family.BEDROCK else AssetKind.MOD
    # If the version's loaders include 'paper'/'spigot' etc, treat as plugin
    if server.family is Family.JAVA and any(
        loader in ("paper", "spigot", "bukkit", "purpur") for loader in versions[0].loaders
    ):
        kind = AssetKind.PLUGIN
    # Auto-snapshot before mutating the data dir. Mod installs can replace
    # existing files; failed installs would otherwise leave no recovery path.
    data_dir = _data_dir(request, server_id)
    backup_mod.create_safety(
        server_id=server_id, data_dir=data_dir, reason="pre_install",
    )
    result = await install_asset(
        data_dir=data_dir,
        family=server.family, kind=kind, version=versions[0],
        client=state(request).http_client,
    )
    record_install(conn, server_id=server_id, source_id=source_id, kind=kind, result=result)
    return TEMPLATES.TemplateResponse(
        request, "servers/tabs/_mods_installed.html",
        {"server": server, "installed": list_installed(conn, server_id)},
    )


@router.delete("/servers/{server_id}/mods/{source_id}/{asset_id}", response_class=HTMLResponse)
async def mods_remove(
    request: Request,
    server_id: str,
    source_id: str,
    asset_id: str,
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    server = _get_server(conn, server_id)  # 404 guard
    remove_installed(conn, server_id, source_id, asset_id)
    return TEMPLATES.TemplateResponse(
        request, "servers/tabs/_mods_installed.html",
        {"server": server, "installed": list_installed(conn, server_id)},
    )


# ─── Backups ────────────────────────────────────────────────────────────────


@router.post("/servers/{server_id}/backups", response_class=HTMLResponse)
async def backups_create(
    request: Request,
    server_id: str,
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    server = _get_server(conn, server_id)
    backup_mod.create(server_id=server_id, data_dir=_data_dir(request, server_id))
    return TEMPLATES.TemplateResponse(
        request, "servers/tabs/_backups_list.html",
        {"server": server, "backups": backup_mod.list_for(server_id)},
    )


@router.post("/servers/{server_id}/backups/{name}/restore", response_class=HTMLResponse)
async def backups_restore(
    request: Request,
    server_id: str,
    name: str,
    lifecycle: Lifecycle = Depends(require_lifecycle),
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    server = _get_server(conn, server_id)
    # Stop the container first if running
    if server.status.value == "running":
        await lifecycle.stop(server_id)
    # Pre-restore snapshot: restore wipes data_dir, so if the chosen backup
    # turns out broken or wrong, this is the only path back to current state.
    data_dir = _data_dir(request, server_id)
    backup_mod.create_safety(
        server_id=server_id, data_dir=data_dir, reason="pre_restore",
    )
    backup_mod.restore(
        server_id=server_id, name=name, data_dir=data_dir,
    )
    return TEMPLATES.TemplateResponse(
        request, "servers/tabs/_backups_list.html",
        {"server": server, "backups": backup_mod.list_for(server_id)},
    )


@router.delete("/servers/{server_id}/backups/{name}", response_class=HTMLResponse)
async def backups_delete(
    request: Request,
    server_id: str,
    name: str,
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    server = _get_server(conn, server_id)
    backup_mod.delete(server_id=server_id, name=name)
    return TEMPLATES.TemplateResponse(
        request, "servers/tabs/_backups_list.html",
        {"server": server, "backups": backup_mod.list_for(server_id)},
    )


# ─── Console WebSocket ──────────────────────────────────────────────────────


@router.websocket("/servers/{server_id}/console-ws")
async def console_ws(websocket: WebSocket, server_id: str) -> None:
    """Streams Docker logs out; accepts commands in (RCON for Java, stdin for
    Bedrock). Lifecycle access reaches in via app.state — WebSocket doesn't
    use FastAPI Depends the same way HTTP routes do."""
    await websocket.accept()
    st = websocket.app.state.ndrchst
    conn = st.conn
    server = srv_store.get(conn, server_id)
    if server is None:
        await websocket.send_text("[ndrchst] server not found")
        await websocket.close()
        return
    if st.lifecycle is None:
        await websocket.send_text(f"[ndrchst] Docker unavailable: {st.docker_error}")
        await websocket.close()
        return

    try:
        # Send the last 200 lines as a backfill
        try:
            recent = await st.lifecycle.logs(server_id, lines=200)
            if recent:
                await websocket.send_text(_log_to_html(recent))
        except Exception as e:
            await websocket.send_text(f"[ndrchst] log fetch failed: {e}")

        while True:
            msg = await websocket.receive_json()
            cmd = (msg.get("command") or "").strip()
            if not cmd:
                continue
            if server.family is Family.JAVA:
                await websocket.send_text(_log_to_html(f"> {cmd}\n[ndrchst] RCON not wired to live container in v0\n"))
            else:
                await websocket.send_text(_log_to_html(f"> {cmd}\n[ndrchst] BDS stdin not wired to live container in v0\n"))
    except WebSocketDisconnect:
        return


def _log_to_html(text: str) -> str:
    """htmx ws extension appends raw HTML directly to the target; we wrap
    each chunk in a div appended to the output container."""
    from html import escape
    return (
        f'<div hx-swap-oob="beforeend:#console-output">'
        f'<span class="mono">{escape(text)}</span>'
        f'</div>'
    )


# ─── Catch-all detail (registered LAST so specific routes match first) ─────


@router.get("/servers/{server_id}/{tab}", response_class=HTMLResponse)
async def detail(
    request: Request,
    server_id: str,
    tab: str,
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    if tab not in _VALID_TABS:
        raise HTTPException(status_code=404, detail=f"unknown tab: {tab}")
    server = _get_server(conn, server_id)
    ctx = await _tab_context(request, server, tab, conn)
    return _render_tab(request, server, tab, f"_{tab}.html", ctx)
