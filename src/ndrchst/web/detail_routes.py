"""Server detail page + all per-tab routes.

Layout: GET /servers/{id}/<tab> returns either a full page (browser nav) or
the tab partial (htmx swap). Mutation endpoints return updated partials.
"""
from __future__ import annotations

import contextlib
import logging
import sqlite3
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..api.deps import db, require_lifecycle, state
from ..domain import config as cfg_mod
from ..domain import files as files_mod
from ..domain import players as players_mod
from ..domain import plugins as plugins_mod
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
from ..runtime.docker import BEDROCK_IMAGE, java_image_for
from ..runtime.lifecycle import SERVERS_ROOT_DEFAULT, Lifecycle
from ..runtime.rcon import RCON, RCONError
from ..store import servers as srv_store

log = logging.getLogger("ndrchst.detail")

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))

router = APIRouter()

_VALID_TABS = ("console", "properties", "players", "worlds", "files", "mods", "plugins", "packs", "config", "backups")


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
    if tab == "plugins":
        data_dir = _data_dir(request, server.id)
        return {"plugins": plugins_mod.list_plugins(data_dir)}
    if tab == "packs":
        data_dir = _data_dir(request, server.id)
        return _packs_ctx(data_dir, server)
    if tab == "config":
        return _config_ctx(request, server)
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


@router.post("/servers/{server_id}/players/ban", response_class=HTMLResponse)
async def players_ban(
    request: Request,
    server_id: str,
    player: str = Form(...),
    reason: str = Form(""),
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    server = _get_server(conn, server_id)
    if server.family is not Family.JAVA:
        raise HTTPException(status_code=400, detail="players UI is Java-only")
    result = await _rcon_call(request, server, lambda r: players_mod.ban(r, player, reason))
    # Refresh the players list so the banned player drops off
    players = await _rcon_call(request, server, players_mod.online, fallback=[])
    return TEMPLATES.TemplateResponse(
        request, "servers/tabs/_players_list.html",
        {"server": server, "players": players, "flash": result or f"Banned {player}"},
    )


@router.post("/servers/{server_id}/players/unban", response_class=HTMLResponse)
async def players_unban(
    request: Request,
    server_id: str,
    player: str = Form(...),
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    server = _get_server(conn, server_id)
    if server.family is not Family.JAVA:
        raise HTTPException(status_code=400, detail="players UI is Java-only")
    result = await _rcon_call(request, server, lambda r: players_mod.unban(r, player))
    return HTMLResponse(content=result or f"Pardoned {player}")


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


# ─── Plugins (Bukkit/Spigot/Paper) ─────────────────────────────────────────


def _plugins_ctx(data_dir: Path) -> dict:
    return {"plugins": plugins_mod.list_plugins(data_dir)}


@router.post("/servers/{server_id}/plugins/{filename}/toggle", response_class=HTMLResponse)
async def plugins_toggle(
    request: Request, server_id: str, filename: str,
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    server = _get_server(conn, server_id)
    if server.family is not Family.JAVA:
        raise HTTPException(status_code=400, detail="plugins are Java-only")
    data_dir = _data_dir(request, server_id)
    try:
        plugins_mod.toggle_plugin(data_dir, filename)
    except plugins_mod.PluginError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return TEMPLATES.TemplateResponse(
        request, "servers/tabs/_plugins_list.html",
        {"server": server, **_plugins_ctx(data_dir)},
    )


@router.delete("/servers/{server_id}/plugins/{filename}", response_class=HTMLResponse)
async def plugins_remove(
    request: Request, server_id: str, filename: str,
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    server = _get_server(conn, server_id)
    if server.family is not Family.JAVA:
        raise HTTPException(status_code=400, detail="plugins are Java-only")
    data_dir = _data_dir(request, server_id)
    try:
        plugins_mod.remove_plugin(data_dir, filename)
    except plugins_mod.PluginError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return TEMPLATES.TemplateResponse(
        request, "servers/tabs/_plugins_list.html",
        {"server": server, **_plugins_ctx(data_dir)},
    )


_PAPER_LOADERS = ["paper", "spigot", "bukkit", "purpur"]


def _major_minor(version: str) -> str:
    """Trim a Paper version like '1.21.11' to '1.21' so Modrinth's loose
    matching catches plugins built against 1.21 minor revisions."""
    parts = version.split(".")
    if len(parts) <= 2:
        return version
    return ".".join(parts[:2])


@router.post("/servers/{server_id}/plugins/check-updates", response_class=HTMLResponse)
async def plugins_check_updates(
    request: Request, server_id: str,
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    """Hash every enabled jar, ask Modrinth for the latest matching build,
    re-render the list with update annotations.

    Plugins whose hash Modrinth doesn't recognise (locally-built or pulled
    from BukkitDev / SpigotMC) simply don't get an annotation — no error.
    """
    server = _get_server(conn, server_id)
    if server.family is not Family.JAVA:
        raise HTTPException(status_code=400, detail="plugins are Java-only")
    data_dir = _data_dir(request, server_id)
    inventory = plugins_mod.hash_inventory(data_dir)
    updates: dict[str, plugins_mod.PluginInfo] = {}
    note: str | None = None
    if inventory:
        source = state(request).modrinth or Modrinth()
        # Try both the exact version and the major.minor pair — plugins
        # commonly mark themselves compatible with `1.21` not `1.21.11`.
        game_versions = [server.version]
        mm = _major_minor(server.version)
        if mm != server.version:
            game_versions.append(mm)
        try:
            latest_by_hash = await source.latest_by_hash(
                list(inventory.values()),
                loaders=_PAPER_LOADERS,
                game_versions=game_versions,
            )
        except (httpx.HTTPError, ValueError) as e:
            log.warning("Modrinth update check failed for %s: %s", server_id, e)
            latest_by_hash = {}
            note = f"Modrinth unreachable: {e}"
        # Re-key by filename so the template can correlate
        for filename, current_hash in inventory.items():
            v = latest_by_hash.get(current_hash)
            if v and v.sha1 and v.sha1 != current_hash:
                updates[filename] = v
    plugins = plugins_mod.list_plugins(data_dir)
    return TEMPLATES.TemplateResponse(
        request, "servers/tabs/_plugins_list.html",
        {"server": server, "plugins": plugins, "updates": updates, "note": note},
    )


@router.post("/servers/{server_id}/plugins/{filename}/update", response_class=HTMLResponse)
async def plugins_update_one(
    request: Request, server_id: str, filename: str,
    download_url: str = Form(...),
    new_filename: str = Form(...),
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    """Download the Modrinth-pointed jar and atomically replace the local
    one. The new filename is taken from Modrinth so version-tagged jars
    (e.g. 'Geyser-Spigot-2.10.1.jar') get the right name on disk."""
    server = _get_server(conn, server_id)
    if server.family is not Family.JAVA:
        raise HTTPException(status_code=400, detail="plugins are Java-only")
    data_dir = _data_dir(request, server_id)
    http = state(request).http_client
    try:
        if http is None:
            http = httpx.AsyncClient(timeout=60.0)
            close_after = True
        else:
            close_after = False
        try:
            r = await http.get(download_url)
            r.raise_for_status()
            new_bytes = r.content
        finally:
            if close_after:
                await http.aclose()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"download failed: {e}") from e
    try:
        plugins_mod.replace_plugin(
            data_dir, filename, new_bytes, new_filename=new_filename,
        )
    except plugins_mod.PluginError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    plugins = plugins_mod.list_plugins(data_dir)
    return TEMPLATES.TemplateResponse(
        request, "servers/tabs/_plugins_list.html",
        {"server": server, "plugins": plugins, "updates": {}, "flash": f"Updated {filename} → {new_filename}"},
    )


@router.post("/servers/{server_id}/plugins/upload", response_class=HTMLResponse)
async def plugins_upload(
    request: Request, server_id: str,
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    from fastapi import UploadFile
    server = _get_server(conn, server_id)
    if server.family is not Family.JAVA:
        raise HTTPException(status_code=400, detail="plugins are Java-only")
    form = await request.form()
    upload: UploadFile = form.get("file")  # type: ignore[assignment]
    if upload is None:
        raise HTTPException(status_code=400, detail="no file uploaded")
    data_dir = _data_dir(request, server_id)
    try:
        plugins_mod.save_upload(data_dir, upload.filename or "uploaded.jar", upload.file)
    except plugins_mod.PluginError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return TEMPLATES.TemplateResponse(
        request, "servers/tabs/_plugins_list.html",
        {"server": server, **_plugins_ctx(data_dir)},
    )


# ─── Packs (datapacks + resource pack URL) ──────────────────────────────────


def _packs_ctx(data_dir: Path, server: Server) -> dict:
    """Datapacks live at world/datapacks/; the server resource-pack is a URL
    field in server.properties (resource-pack=...)."""
    world_name = "world"
    props = props_mod.read(data_dir)
    if "level-name" in props:
        world_name = props["level-name"]
    dp_dir = data_dir / world_name / "datapacks"
    datapacks = []
    if dp_dir.exists():
        for p in sorted(dp_dir.iterdir()):
            if p.is_dir() or (p.is_file() and p.suffix.lower() == ".zip"):
                datapacks.append({
                    "name": p.name,
                    "size": p.stat().st_size if p.is_file() else None,
                })
    return {
        "datapacks": datapacks,
        "world_name": world_name,
        "resource_pack_url": props.get("resource-pack", ""),
        "resource_pack_sha1": props.get("resource-pack-sha1", ""),
    }


@router.post("/servers/{server_id}/packs/datapack/upload", response_class=HTMLResponse)
async def datapack_upload(
    request: Request, server_id: str,
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    from fastapi import UploadFile
    server = _get_server(conn, server_id)
    if server.family is not Family.JAVA:
        raise HTTPException(status_code=400, detail="datapacks are Java-only")
    form = await request.form()
    upload: UploadFile = form.get("file")  # type: ignore[assignment]
    if upload is None or not upload.filename:
        raise HTTPException(status_code=400, detail="no file uploaded")
    if not upload.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="datapack must be a .zip")
    safe = upload.filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "/" in safe or "\\" in safe or safe.startswith("."):
        raise HTTPException(status_code=400, detail="unsafe filename")
    data_dir = _data_dir(request, server_id)
    props = props_mod.read(data_dir)
    world_name = props.get("level-name", "world")
    dp_dir = data_dir / world_name / "datapacks"
    dp_dir.mkdir(parents=True, exist_ok=True)
    target = dp_dir / safe
    import shutil
    with target.open("wb") as f:
        shutil.copyfileobj(upload.file, f)
    return TEMPLATES.TemplateResponse(
        request, "servers/tabs/_packs.html",
        {"server": server, **_packs_ctx(data_dir, server)},
    )


@router.delete("/servers/{server_id}/packs/datapack/{filename}", response_class=HTMLResponse)
async def datapack_remove(
    request: Request, server_id: str, filename: str,
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    server = _get_server(conn, server_id)
    data_dir = _data_dir(request, server_id)
    props = props_mod.read(data_dir)
    world_name = props.get("level-name", "world")
    target = (data_dir / world_name / "datapacks" / filename).resolve()
    safe_base = (data_dir / world_name / "datapacks").resolve()
    if safe_base not in target.parents and target != safe_base:
        raise HTTPException(status_code=400, detail="path escape")
    if not target.exists():
        raise HTTPException(status_code=404, detail="datapack not found")
    if target.is_dir():
        import shutil
        shutil.rmtree(target)
    else:
        target.unlink()
    return TEMPLATES.TemplateResponse(
        request, "servers/tabs/_packs.html",
        {"server": server, **_packs_ctx(data_dir, server)},
    )


@router.post("/servers/{server_id}/packs/resource-pack", response_class=HTMLResponse)
async def resource_pack_set(
    request: Request, server_id: str,
    url: str = Form(""),
    sha1: str = Form(""),
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    """Set or clear the server resource-pack URL in server.properties.
    Empty url clears both keys."""
    server = _get_server(conn, server_id)
    data_dir = _data_dir(request, server_id)
    props_mod.write(data_dir, {"resource-pack": url, "resource-pack-sha1": sha1})
    return TEMPLATES.TemplateResponse(
        request, "servers/tabs/_packs.html",
        {"server": server, **_packs_ctx(data_dir, server)},
    )


# ─── Config (memory + JVM + env) ────────────────────────────────────────────


def _config_ctx(request: Request, server: Server) -> dict:
    """Read-only summary + editable fields. Mounts/ports are derived from the
    current spec so the user can sanity-check what the container actually has."""
    data_dir = _data_dir(request, server.id)
    summary = {
        "image_hint": java_image_for(server.version)
            if server.family is Family.JAVA else BEDROCK_IMAGE,
        "data_dir": str(data_dir),
        "container_id": server.container_id or "(not created)",
        "ports": _summarize_ports(server),
        "rcon_port": server.rcon_port,
    }
    return {
        "server": server,
        "summary": summary,
        "extra_jvm_flags": server.extra_jvm_flags or "",
        "env_vars": server.env_vars or "",
    }


def _summarize_ports(server: Server) -> list[str]:
    rows: list[str] = []
    if server.family is Family.JAVA:
        rows.append(f"25565/tcp (game) → 0.0.0.0:{server.port}")
        if server.cross_play and server.bedrock_bridge_port:
            rows.append(f"19132/udp (bedrock bridge) → 0.0.0.0:{server.bedrock_bridge_port}")
        if server.rcon_port:
            rows.append(f"25575/tcp (rcon) → 127.0.0.1:{server.rcon_port}")
    else:
        rows.append(f"19132/udp (game) → 0.0.0.0:{server.port}")
    return rows


@router.post("/servers/{server_id}/config", response_class=HTMLResponse)
async def config_save(
    request: Request,
    server_id: str,
    memory_mb: int = Form(...),
    extra_jvm_flags: str = Form(""),
    env_vars: str = Form(""),
    lifecycle: Lifecycle = Depends(require_lifecycle),
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    server = _get_server(conn, server_id)
    try:
        server = await lifecycle.update_config(
            server_id,
            memory_mb=memory_mb,
            extra_jvm_flags=extra_jvm_flags or None,
            env_vars=env_vars or None,
        )
    except cfg_mod.ConfigError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    ctx = _config_ctx(request, server)
    return TEMPLATES.TemplateResponse(
        request, "servers/tabs/_config.html",
        {**ctx, "flash": "Saved. Container recreated — click Start to apply."},
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


@router.get("/servers/{server_id}/logs/download")
async def logs_download(server_id: str, request: Request, lines: int = 10000):
    """Download the container's recent log as a plain-text file. ``lines``
    bounds it (default 10k); use lines=0 for "everything we can get from
    the docker engine". Docker keeps logs per its log-driver retention so
    'full log' really means 'whatever docker still has on disk'."""
    from fastapi.responses import PlainTextResponse
    st = request.app.state.ndrchst
    if st.lifecycle is None:
        raise HTTPException(status_code=503, detail="Docker unavailable")
    server = srv_store.get(st.conn, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="server not found")
    # tail=0 in docker-py means "no tail"; we use a very large number for "all".
    n = lines if lines and lines > 0 else 1_000_000
    text = await st.lifecycle.logs(server_id, lines=n)
    headers = {
        "Content-Disposition": f'attachment; filename="{server.name.replace(" ", "_")}-{server_id}.log"',
    }
    return PlainTextResponse(text, headers=headers)


@router.websocket("/servers/{server_id}/console-ws")
async def console_ws(websocket: WebSocket, server_id: str) -> None:
    """Streams Docker logs live; accepts commands in (RCON for Java, stdin for
    Bedrock). Lifecycle access reaches in via app.state — WebSocket doesn't
    use FastAPI Depends the same way HTTP routes do."""
    import asyncio as _asyncio
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

    async def _tail_logs() -> None:
        """Pump live container logs into the WS. The follow generator emits
        chunks that may contain partial lines; we forward as-is and the
        client just appends."""
        try:
            async for chunk in st.lifecycle.follow_logs(server_id, tail=200):
                if not chunk:
                    continue
                await websocket.send_text(_log_to_html(chunk))
        except _asyncio.CancelledError:
            raise
        except Exception as e:
            with contextlib.suppress(Exception):
                await websocket.send_text(_log_to_html(
                    f"[ndrchst] log stream ended: {type(e).__name__}: {e}\n"
                ))

    tail_task = _asyncio.create_task(_tail_logs())
    try:
        while True:
            msg = await websocket.receive_json()
            cmd = (msg.get("command") or "").strip()
            if not cmd:
                continue
            # Trim leading slash — Bukkit doesn't want it in RCON, BDS doesn't either
            if cmd.startswith("/"):
                cmd = cmd[1:]
            await websocket.send_text(_log_to_html(f"> {cmd}\n"))
            try:
                if server.family is Family.JAVA:
                    if server.rcon_port is None or server.rcon_password is None:
                        await websocket.send_text(_log_to_html(
                            "[ndrchst] this server predates RCON support — recreate it to enable console commands\n"
                        ))
                        continue
                    from ..runtime.rcon import RCON, AuthError, RCONError
                    try:
                        async with RCON("127.0.0.1", server.rcon_port, server.rcon_password, timeout=8.0) as r:
                            response = await r.command(cmd)
                    except AuthError:
                        await websocket.send_text(_log_to_html(
                            "[ndrchst] RCON auth rejected — password mismatch (server.properties drift?)\n"
                        ))
                        continue
                    except (TimeoutError, RCONError, OSError) as e:
                        await websocket.send_text(_log_to_html(
                            f"[ndrchst] RCON error: {type(e).__name__}: {e}\n"
                        ))
                        continue
                    if response.strip():
                        await websocket.send_text(_log_to_html(response.rstrip() + "\n"))
                else:
                    # Bedrock: pipe to BDS stdin; output will surface via the
                    # live log tail above.
                    try:
                        await st.lifecycle._docker.send_stdin(server.container_id, cmd)
                    except Exception as e:
                        await websocket.send_text(_log_to_html(
                            f"[ndrchst] stdin error: {type(e).__name__}: {e}\n"
                        ))
                        continue
            except Exception as e:
                await websocket.send_text(_log_to_html(
                    f"[ndrchst] unexpected error: {type(e).__name__}: {e}\n"
                ))
    except WebSocketDisconnect:
        return
    finally:
        tail_task.cancel()
        with contextlib.suppress(_asyncio.CancelledError, Exception):
            await tail_task


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
