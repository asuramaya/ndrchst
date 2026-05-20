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

import asyncio
import contextlib
import ipaddress
import logging
import os
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .domain import auth_session, device_token, join_token, pilot_pairing, wallet
from .domain.models import Family
from .logging_setup import configure as configure_logging
from .runtime import solana
from .runtime.holdings_refresh import refresh_all_holdings
from .runtime.pilot import bundle_path as pilot_bundle_path
from .store import daily_claims as dc_store
from .store import servers as srv_store
from .store import wallet_links as wl_store
from .store.db import DEFAULT_DB_PATH, connect
from .web.public_pages import render_landing, render_link, render_play, render_ranks

_SNAPSHOT_INTERVAL = int(os.environ.get("NDRCHST_SNAPSHOT_INTERVAL", "3600"))
# End-themed game assets (gitignored; staged to the box + R2). Served at /game
# for origin/local; the edge Worker serves the same keys from R2.
_GAME_DIR = Path(__file__).resolve().parent / "web" / "static" / "game"

_SESSION_COOKIE = "ndrchst_session"


class _ChallengeReq(BaseModel):
    pubkey: str


class _VerifyReq(BaseModel):
    pubkey: str
    message: str
    signature: str  # base64 of the raw ed25519 signature bytes


class _PilotApproveReq(_VerifyReq):
    code: str  # the pairing user_code shown by the pilot


class _JoinVerifyReq(BaseModel):
    token: str  # the join token the ndrchst-auth mod received from the client


class _DeviceExchangeReq(BaseModel):
    device_token: str  # the pilot's long-lived credential


class _DailyClaimReq(BaseModel):
    wallet: str  # the verified wallet the mod stashed at login


class _DailyResetReq(BaseModel):
    wallet: str


def _cookie_secure() -> bool:
    return os.environ.get("NDRCHST_COOKIE_SECURE", "1") != "0"


def _cookie_domain() -> str | None:
    """Optional cookie Domain. Set NDRCHST_COOKIE_DOMAIN=.ndrchst.com so one
    sign-in is recognized across the apex landing, play, and www. Unset (the
    default) scopes the cookie to the current host — correct for dev/tests."""
    return os.environ.get("NDRCHST_COOKIE_DOMAIN") or None


def _is_internal_caller(request: Request) -> bool:
    """True iff the request came from the box's own container network (the
    ndrchst-auth mod), not the public internet.

    `origin.ndrchst.com` is a catch-all tunnel to this app, so /join/verify and
    /daily/* are reachable from the internet. The mod, however, calls in from
    its Docker container — source IP in the private bridge range (172.16/12,
    10/8, 192.168/16). The public tunnel arrives via cloudflared on the host →
    loopback. Holder wallets are public on-chain, so an unauthenticated
    /daily/claim would let anyone burn every holder's cooldown; gate it to the
    bridge. Non-IP clients (TestClient) are treated as internal for tests."""
    host = request.client.host if request.client else ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True  # e.g. TestClient "testclient" — not a public route
    return ip.is_private and not ip.is_loopback


def _site_base() -> str:
    return os.environ.get("NDRCHST_SITE_BASE", "https://play.ndrchst.com").rstrip("/")


def _verify_signed_or_raise(pubkey: str, message: str, signature_b64: str) -> None:
    """Shared SIWS check used by /auth/verify and /pilot/auth/approve: the
    message must carry a server-issued single-use nonce and be exactly the
    challenge we'd build, and the signature must verify for the wallet."""
    import base64

    if not wallet.is_valid_pubkey(pubkey):
        raise HTTPException(status_code=400, detail="invalid wallet address")
    nonce = ""
    for line in message.splitlines():
        if line.startswith("Nonce: "):
            nonce = line[len("Nonce: "):].strip()
            break
    if not nonce or not auth_session.consume_nonce(nonce):
        raise HTTPException(status_code=401, detail="challenge expired or unknown")
    if message != auth_session.build_message(pubkey, nonce):
        raise HTTPException(status_code=401, detail="challenge mismatch")
    try:
        sig = base64.b64decode(signature_b64)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail="bad signature encoding") from e
    if not wallet.verify_signature(pubkey, message.encode(), sig):
        raise HTTPException(status_code=401, detail="signature verification failed")


def _run_snapshot(db_path: Path) -> int:
    """Refresh + persist the holdings snapshot for every linked wallet on a
    short-lived connection (called from a worker thread). Returns the count."""
    conn = connect(db_path)
    try:
        return len(refresh_all_holdings(conn))
    finally:
        conn.close()


def _identity(pubkey: str) -> dict:
    """Public identity view for a wallet: display handle, derived MC name,
    holdings %, and rank tier. Holdings is a live RPC read (0.0 on any miss)."""
    pct = solana.holdings_pct(pubkey)
    tier = wallet.tier_for(pct)
    return {
        "wallet": pubkey,
        "display": wallet.abbreviate(pubkey),
        "mc_name": wallet.derive_mc_name(pubkey),
        "holdings_pct": round(pct, 6),
        "tier": tier.key if tier else None,
        "tier_name": tier.name if tier else None,
    }


def create_public_app(*, db_path: Path | None = None) -> FastAPI:
    """Factory. The public app keeps its own SQLite connection (read-only)."""
    conn_holder: dict[str, sqlite3.Connection] = {}
    _db_path = db_path or DEFAULT_DB_PATH
    _log = logging.getLogger("ndrchst.public")

    async def _snapshot_loop() -> None:
        """Re-read every linked wallet's chain holdings on a fixed cadence and
        persist the hourly snapshot daily rewards read from. Runs on its own
        connection (the blocking RPC + writes go through to_thread so the event
        loop isn't stalled)."""
        while True:
            await asyncio.sleep(_SNAPSHOT_INTERVAL)
            try:
                n = await asyncio.to_thread(_run_snapshot, _db_path)
                _log.info("holdings snapshot: refreshed %d wallet(s)", n)
            except Exception:
                # A flaky RPC or transient DB error must not kill the loop.
                _log.exception("holdings snapshot failed")

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        # Footgun guard: with a secure-cookie (prod) deploy and no signing
        # secret, auth_session falls back to a random per-process secret —
        # every restart silently invalidates all sessions. Warn loudly.
        if _cookie_secure() and not os.environ.get("NDRCHST_SESSION_SECRET"):
            _log.warning(
                "NDRCHST_SESSION_SECRET is unset — using a random per-process "
                "secret; all wallet logins will drop on restart. Set it in the "
                "service EnvironmentFile to persist sessions.")
        conn_holder["conn"] = connect(_db_path)
        task = asyncio.create_task(_snapshot_loop()) if _SNAPSHOT_INTERVAL > 0 else None
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            conn_holder["conn"].close()
            conn_holder.clear()

    app = FastAPI(
        title="ndrchst public", version="0.0.1",
        docs_url=None, redoc_url=None,
        lifespan=lifespan,
    )

    if _GAME_DIR.is_dir():
        app.mount("/game", StaticFiles(directory=_GAME_DIR), name="game")

    def _conn() -> sqlite3.Connection:
        return conn_holder["conn"]

    def _with_join_token(ident: dict) -> dict:
        """Attach a short-lived join token to an identity for the pilot to
        carry — the credential the ndrchst-auth mod presents at connect time."""
        return {
            **ident,
            "join_token": join_token.issue(
                ident["wallet"], ident["mc_name"], ident.get("tier")),
        }

    def _record_link(ident: dict) -> None:
        """Persist a wallet's identity + rank so the admin can sync it to game
        servers (whitelist/rank). Best-effort: never block auth on a DB hiccup."""
        with contextlib.suppress(sqlite3.Error):
            wl_store.upsert(_conn(), ident["wallet"], ident["mc_name"],
                            ident.get("tier"), ident.get("holdings_pct", 0.0))

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "surface": "public"}

    # --- wallet auth (Sign-In With Solana) -----------------------------------
    @app.post("/auth/challenge")
    def auth_challenge(req: _ChallengeReq = Body(...)) -> JSONResponse:
        """Issue a single-use challenge message for the wallet to sign."""
        if not wallet.is_valid_pubkey(req.pubkey):
            raise HTTPException(status_code=400, detail="invalid wallet address")
        nonce = auth_session.issue_nonce()
        message = auth_session.build_message(req.pubkey, nonce)
        return JSONResponse({"message": message}, headers=_NO_STORE)

    @app.post("/auth/verify")
    def auth_verify(req: _VerifyReq = Body(...)) -> JSONResponse:
        """Verify the signed challenge, then set a session cookie."""
        _verify_signed_or_raise(req.pubkey, req.message, req.signature)
        ident = _identity(req.pubkey)
        _record_link(ident)
        token = auth_session.sign_session(req.pubkey)
        resp = JSONResponse(ident, headers=_NO_STORE)
        resp.set_cookie(
            _SESSION_COOKIE, token, max_age=7 * 24 * 3600, httponly=True,
            samesite="lax", secure=_cookie_secure(), path="/",
            domain=_cookie_domain(),
        )
        return resp

    # --- desktop pilot pairing (OAuth device-flow shape) ---------------------
    @app.post("/pilot/auth/start")
    def pilot_auth_start() -> JSONResponse:
        """The pilot begins a pairing; it opens verify_url in a browser and
        polls with pair_id until a wallet is bound."""
        pair_id, user_code = pilot_pairing.start()
        return JSONResponse({
            "pair_id": pair_id,
            "user_code": user_code,
            "verify_url": f"{_site_base()}/link?code={user_code}",
            "interval": 2,
            "expires_in": 600,
        }, headers=_NO_STORE)

    @app.get("/link", response_class=HTMLResponse)
    def link_page(code: str = "") -> HTMLResponse:
        """Web page where the user connects a wallet to approve a pilot."""
        return HTMLResponse(render_link(code=code))

    @app.post("/pilot/auth/approve")
    def pilot_auth_approve(req: _PilotApproveReq = Body(...)) -> JSONResponse:
        """Verify the wallet signature and bind it to the pairing code."""
        _verify_signed_or_raise(req.pubkey, req.message, req.signature)
        if not pilot_pairing.approve(req.code, req.pubkey):
            raise HTTPException(status_code=404, detail="unknown or expired pairing code")
        ident = _identity(req.pubkey)
        _record_link(ident)
        return JSONResponse({"ok": True, **_with_join_token(ident)}, headers=_NO_STORE)

    @app.get("/pilot/auth/poll")
    def pilot_auth_poll(pair_id: str) -> JSONResponse:
        """The pilot polls until its pairing is approved."""
        p = pilot_pairing.poll(pair_id)
        if p is None:
            raise HTTPException(status_code=404, detail="unknown or expired pairing")
        if not p.pubkey:
            return JSONResponse({"status": "pending"}, headers=_NO_STORE)
        ident = _with_join_token(_identity(p.pubkey))
        return JSONResponse({"status": "approved", **ident}, headers=_NO_STORE)

    @app.post("/join/verify")
    def join_verify(request: Request, req: _JoinVerifyReq = Body(...)) -> JSONResponse:
        """Called by the ndrchst-auth server mod at connect time: validate the
        join token the client presented. Returns the bound identity + rank on
        success, 401 otherwise. This is the gate — the mod rejects the
        connection on a non-200. Mod-only (bridge); not a public route."""
        if not _is_internal_caller(request):
            raise HTTPException(status_code=403, detail="not an internal caller")
        claims = join_token.verify(req.token)
        if claims is None:
            raise HTTPException(status_code=401, detail="invalid or expired join token")
        # Rank from the latest known tier (refreshed at sign-in + hourly), not a
        # live RPC on the connect path. Identity is pinned by the signed token;
        # floor to the base tier so a linked wallet is never rankless.
        link = wl_store.get(_conn(), claims["wallet"])
        tier_key = link.tier if (link and link.tier) else "holder"
        return JSONResponse(
            {
                "ok": True,
                "wallet": claims["wallet"],
                "mc_name": claims["mc_name"],
                "tier": tier_key,
            },
            headers=_NO_STORE,
        )

    @app.post("/device/exchange")
    def device_exchange(req: _DeviceExchangeReq = Body(...)) -> JSONResponse:
        """The pilot trades its long-lived device token for a FRESH short-lived
        join token (tier re-read from chain) at Play — so the gate credential
        is never stale and there's no in-launcher device-flow round-trip."""
        wallet_pk = device_token.verify(req.device_token)
        if not wallet_pk:
            raise HTTPException(status_code=401, detail="invalid or expired device token")
        ident = _identity(wallet_pk)
        _record_link(ident)
        return JSONResponse(_with_join_token(ident), headers=_NO_STORE)

    @app.post("/daily/claim")
    def daily_claim(request: Request, req: _DailyClaimReq = Body(...)) -> JSONResponse:
        """Called by the mod's /daily: enforce the durable 24h cooldown and
        return the reward tier (the hourly SNAPSHOT tier — not the on-demand
        latest — so it can't be refreshed mid-session to farm the carousel).
        Mod-only (bridge); the cooldown is the mutable state we must protect
        from the public tunnel since holder wallets are on-chain-visible."""
        if not _is_internal_caller(request):
            raise HTTPException(status_code=403, detail="not an internal caller")
        # Snapshot tier, floored to the base so even a brand-new / not-yet-
        # snapshotted wallet still earns the base daily ("just showing up").
        link = wl_store.get(_conn(), req.wallet)
        tier_key = link.snapshot_tier if (link and link.snapshot_tier) else "holder"
        conn = connect(_db_path)
        try:
            ok, seconds_left = dc_store.try_claim(conn, req.wallet)
        finally:
            conn.close()
        return JSONResponse(
            {"ok": ok, "tier": tier_key, "seconds_left": seconds_left},
            headers=_NO_STORE,
        )

    @app.post("/daily/reset")
    def daily_reset(request: Request, req: _DailyResetReq = Body(...)) -> JSONResponse:
        """Op escape hatch (mod `/ndrchst daily reset`): clear a wallet's
        cooldown. Mod-only (bridge)."""
        if not _is_internal_caller(request):
            raise HTTPException(status_code=403, detail="not an internal caller")
        conn = connect(_db_path)
        try:
            dc_store.reset(conn, req.wallet)
        finally:
            conn.close()
        return JSONResponse({"ok": True}, headers=_NO_STORE)

    @app.get("/me/pilot/{server_id}")
    def me_pilot(server_id: str, request: Request) -> Response:
        """Authenticated, personalized pilot download: requires a wallet session
        (sign in on the play page first) and bakes a device token into the
        bundle so the launcher is already linked — no separate sign-in step."""
        import io
        import zipfile

        wallet_pk = auth_session.verify_session(request.cookies.get(_SESSION_COOKIE))
        if not wallet_pk:
            raise HTTPException(status_code=401, detail="sign in with your wallet to download")
        server = srv_store.get(_conn(), server_id)
        if server is None:
            raise HTTPException(status_code=404, detail="server not found")
        base = pilot_bundle_path(server_id)
        if base is None:
            raise HTTPException(status_code=404, detail="pilot bundle not built for this server")
        buf = io.BytesIO()
        with zipfile.ZipFile(base) as src, \
                zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
            for item in src.infolist():
                out.writestr(item, src.read(item.filename))
            out.writestr("ndrchst-device.token", device_token.issue(wallet_pk))
        fname = f"ndrchst-pilot-{server.name.replace(' ', '_')}.zip"
        return Response(
            content=buf.getvalue(),
            media_type="application/zip",
            headers={**_NO_STORE, "Content-Disposition": f'attachment; filename="{fname}"'},
        )

    @app.get("/me")
    def me(request: Request) -> JSONResponse:
        """Current wallet identity from the session cookie, or 401."""
        pubkey = auth_session.verify_session(request.cookies.get(_SESSION_COOKIE))
        if not pubkey:
            raise HTTPException(status_code=401, detail="not signed in")
        return JSONResponse(_identity(pubkey), headers=_NO_STORE)

    @app.post("/auth/logout")
    def auth_logout() -> JSONResponse:
        resp = JSONResponse({"ok": True}, headers=_NO_STORE)
        resp.delete_cookie(_SESSION_COOKIE, path="/", domain=_cookie_domain())
        return resp

    def _play_servers() -> list[dict]:
        return [
            {
                "id": s.id,
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
        return HTMLResponse(render_landing())

    @app.get("/play", response_class=HTMLResponse)
    def play() -> HTMLResponse:
        import os
        downloads_base = os.environ.get("NDRCHST_PILOT_DOWNLOADS_BASE", "")
        return HTMLResponse(render_play(_play_servers(), downloads_base=downloads_base))

    @app.get("/ranks", response_class=HTMLResponse)
    def ranks() -> HTMLResponse:
        """Public rank surface: the tier ladder + a leaderboard of linked
        holders. Reads the stored snapshot (refreshed admin-side), not live
        chain — so loading the page never fans out RPC calls."""
        tier_names = {t.key: t.name for t in wallet.DEFAULT_TIERS}
        tiers = [
            {"key": t.key, "name": t.name, "min_pct": t.min_pct}
            for t in wallet.DEFAULT_TIERS
        ]
        holders = [
            {
                "display": wallet.abbreviate(link.wallet),
                "mc_name": link.mc_name,
                "tier": link.tier,
                "tier_name": tier_names.get(link.tier) if link.tier else None,
                "holdings_pct": link.holdings_pct,
            }
            for link in wl_store.list_all(_conn())
            if link.holdings_pct and link.holdings_pct > 0
        ]
        holders.sort(key=lambda h: h["holdings_pct"], reverse=True)
        return HTMLResponse(render_ranks(holders, tiers))

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
