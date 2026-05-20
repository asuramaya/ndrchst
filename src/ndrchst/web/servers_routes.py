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
from ..runtime.holdings_refresh import refresh_all_holdings
from ..runtime.lifecycle import CreateRequest, Lifecycle, LifecycleError
from ..runtime.rcon import RCONError
from ..runtime.whitelist_sync import sync_links_to_server
from ..store import servers as srv_store
from ..store import wallet_links as wl_store

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


@router.get("/servers/new/versions", response_class=HTMLResponse)
async def new_form_versions(platform_id: str) -> HTMLResponse:
    """htmx fragment: <option> elements for the version datalist, freshly
    fetched from the platform's upstream API. Trigger: select#platform_id
    change (and initial load)."""
    if platform_id not in PLATFORMS:
        return HTMLResponse("")
    platform = PLATFORMS[platform_id]
    if not platform.implemented:
        return HTMLResponse('<option value="latest">latest</option>')
    try:
        versions = await platform.versions()
    except Exception:
        # Soft fail: empty datalist; the user can still type a version.
        return HTMLResponse('<option value="latest">latest</option>')
    opts = ['<option value="latest">latest (auto-resolves)</option>']
    for v in versions[:50]:
        label = f"{v.version}"
        if not v.stable:
            label += " (snapshot)"
        opts.append(f'<option value="{v.version}">{label}</option>')
    return HTMLResponse("".join(opts))


@router.post("/servers", response_class=HTMLResponse)
async def create(
    request: Request,
    name: str = Form(...),
    platform_id: str = Form(...),
    version: str = Form("latest"),
    port: int = Form(...),
    memory_mb: int = Form(2048),
    cross_play: bool = Form(False),
    bedrock_bridge_port: int = Form(19132),
    lifecycle: Lifecycle = Depends(require_lifecycle),
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    try:
        await lifecycle.create(CreateRequest(
            name=name, platform_id=platform_id, version=version,
            port=port, memory_mb=memory_mb, cross_play=cross_play,
            bedrock_bridge_port=bedrock_bridge_port,
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


@router.get("/servers/{server_id}/stats", response_class=HTMLResponse)
async def stats_fragment(
    server_id: str,
    lifecycle: Lifecycle = Depends(require_lifecycle),
) -> HTMLResponse:
    """Tiny htmx-poll fragment: CPU% + memory used/limit. The full card
    polls this every 5s while the server is running."""
    try:
        s = await lifecycle.stats(server_id)
    except LifecycleError:
        return HTMLResponse('<span class="mono" style="color: var(--text-muted);">—</span>')
    if s is None:
        return HTMLResponse('<span class="mono" style="color: var(--text-muted);">no container</span>')
    bar_pct = min(100, int((s.memory_used_mb / max(s.memory_limit_mb, 1)) * 100))
    return HTMLResponse(
        f'<span class="mono" title="CPU usage">⚡ {s.cpu_percent:.1f}%</span>'
        f'<span class="mono" title="Memory used / limit">'
        f'🧠 {s.memory_used_mb}M / {s.memory_limit_mb}M ({bar_pct}%)</span>'
    )


@router.post("/servers/{server_id}/restart", response_class=HTMLResponse)
async def restart(
    request: Request,
    server_id: str,
    lifecycle: Lifecycle = Depends(require_lifecycle),
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    try:
        await lifecycle.restart(server_id)
    except LifecycleError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _render_card(request, conn, server_id)


@router.post("/servers/{server_id}/mods/build-index", response_class=HTMLResponse)
async def build_mods_index(
    request: Request,
    server_id: str,
    lifecycle: Lifecycle = Depends(require_lifecycle),
) -> HTMLResponse:
    """(Re)build the cached mods-index.json: resolves each mod to a
    CurseForge CDN URL (or our origin for substitutions) so pilots pull
    bytes from CF's global CDN instead of through the operator's uplink.
    Run after the mod set changes (install, substitution, version bump)."""
    try:
        total, cdn, client_only = await lifecycle.build_mods_index(server_id)
    except LifecycleError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    msg = (
        f"Built mods index: {total} mods ({cdn} from CDN, "
        f"{total - cdn} from origin, {client_only} client-only)"
    )
    # Auto-publish the light set (index + pages) to R2 if configured, so the
    # edge reflects the new index immediately. Best-effort — never fail the
    # rebuild on a publish hiccup.
    try:
        pub = await lifecycle.publish_to_r2(server_id)
        if pub.get("published"):
            msg += f" · published {pub.get('uploaded', 0)} objects to R2"
    except Exception as e:
        msg += f" · R2 publish skipped ({e})"
    return HTMLResponse(msg)


@router.post("/servers/{server_id}/r2-publish", response_class=HTMLResponse)
async def r2_publish(
    request: Request,
    server_id: str,
    lifecycle: Lifecycle = Depends(require_lifecycle),
) -> HTMLResponse:
    """Publish pilot artifacts (incl. pilot.zip) + public pages to Cloudflare
    R2. The big modpack is never pushed — the pilot pulls it from CF's CDN."""
    try:
        result = await lifecycle.publish_to_r2(server_id)
    except LifecycleError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not result.get("published"):
        return HTMLResponse(f"Not published: {result.get('reason')}")
    return HTMLResponse(f"Published {result.get('uploaded', 0)} objects to R2")


@router.post("/servers/{server_id}/wallets/sync", response_class=HTMLResponse)
async def wallets_sync(
    server_id: str,
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    """Push every linked wallet to this server's whitelist (and rank, if
    NDRCHST_RANK_CMD is set) over RCON. Does not enable whitelist enforcement."""
    server = srv_store.get(conn, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="server not found")
    if server.rcon_port is None or server.rcon_password is None:
        raise HTTPException(status_code=400, detail="server has no RCON configured")
    links = wl_store.list_all(conn)
    if not links:
        return HTMLResponse("No linked wallets to sync.")
    try:
        results = await sync_links_to_server(
            "127.0.0.1", server.rcon_port, server.rcon_password, links)
    except (RCONError, OSError) as e:
        raise HTTPException(status_code=502, detail=f"RCON unavailable: {e}") from e
    synced = 0
    ranked = 0
    for link, res in zip(links, results, strict=True):
        if res.whitelisted:
            wl_store.mark_synced(conn, link.wallet)
            synced += 1
        if res.ranked:
            ranked += 1
    return HTMLResponse(f"Synced {synced}/{len(links)} wallets to whitelist; {ranked} ranked.")


@router.post("/wallets/refresh", response_class=HTMLResponse)
def wallets_refresh(
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    """Re-read every linked wallet's on-chain holdings and recompute its tier.
    Run before a whitelist/rank sync so sellers lose rank and buyers gain it.
    Reads the chain once per wallet — single-operator admin, no batching."""
    results = refresh_all_holdings(conn)
    if not results:
        return HTMLResponse("No linked wallets to refresh.")
    changed = sum(1 for r in results if r.changed)
    return HTMLResponse(
        f"Refreshed {len(results)} wallets; {changed} tier change(s). "
        f"Run wallet sync to push the new ranks.")


@router.post("/servers/{server_id}/container/recreate", response_class=HTMLResponse)
async def container_recreate(
    request: Request,
    server_id: str,
    lifecycle: Lifecycle = Depends(require_lifecycle),
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    """Stop + remove + recreate the docker container from the current
    spec. Data dir is preserved. Useful when ndrchst upgrades change the
    container cmd/env and existing servers need to pick up the new spec
    without losing world data."""
    try:
        server = await lifecycle.recreate_container(server_id)
    except LifecycleError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _render_card(request, conn, server.id)


@router.post("/servers/{server_id}/pilot/regenerate", response_class=HTMLResponse)
async def regenerate_pilot(
    request: Request,
    server_id: str,
    modpack_url: str = Form(""),
    cf_project_id: int | None = Form(None),
    cf_file_id: int | None = Form(None),
    neoforge_version: str = Form(""),
    lifecycle: Lifecycle = Depends(require_lifecycle),
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    """Rebuild the pilot bundle for this server from the *current* lifespan
    env (NDRCHST_PUBLIC_HOST + NDRCHST_EDGE_URL + NDRCHST_TUNNEL_HOSTNAME).

    Modpack source: pass ``cf_project_id`` + ``cf_file_id`` to pin a
    CurseForge client pack — they're persisted and resolved to a CF CDN URL
    at build time (the box never re-hosts the ~200MB pack). An explicit
    ``modpack_url`` still wins as a manual override."""
    from ..runtime.pilot import PilotBuildError
    if cf_project_id and cf_file_id:
        lifecycle.set_modpack_pack(server_id, cf_project_id, cf_file_id)
    if not modpack_url:
        try:
            resolved = await lifecycle.modpack_cdn_url(server_id)
        except Exception as e:
            raise HTTPException(
                status_code=502,
                detail=f"could not resolve CurseForge pack URL: {e}",
            ) from e
        if resolved:
            modpack_url = resolved
    try:
        bundle = lifecycle.regenerate_pilot(
            server_id,
            modpack_url=modpack_url,
            neoforge_version=neoforge_version,
        )
    except LifecycleError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except PilotBuildError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    src = f" · modpack from {modpack_url}" if modpack_url else ""
    return HTMLResponse(
        f"Rebuilt pilot bundle ({bundle.size} bytes, "
        f"sha256={bundle.sha256[:12]}…){src}",
    )


@router.put("/servers/{server_id}/name", response_class=HTMLResponse)
async def rename(
    request: Request,
    server_id: str,
    name: str = Form(...),
    conn: sqlite3.Connection = Depends(db),
) -> HTMLResponse:
    """Inline rename. No container touch — just DB."""
    server = srv_store.get(conn, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="server not found")
    # Reuse the lifecycle name regex by parsing through _validate? It's bound
    # to CreateRequest; simpler to inline the same constraint here.
    import re
    if not re.match(r"^[A-Za-z0-9 _-]{1,64}$", name):
        raise HTTPException(
            status_code=400,
            detail="name must be 1-64 chars of [A-Za-z0-9 _-]",
        )
    conn.execute("UPDATE servers SET name = ? WHERE id = ?", (name, server_id))
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
