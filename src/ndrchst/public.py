"""Public ndrchst surface — for end users, not admins.

Boots as a second FastAPI app on a different port (env NDRCHST_PUBLIC_PORT,
default 8081). Read-only: lists running servers, serves per-server client
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
import json
import logging
import os
import sqlite3
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .domain import (
    auth_session,
    client_handoff,
    client_pairing,
    device_token,
    identity_pairing,
    join_token,
    wallet,
)
from .domain.models import Family
from .logging_setup import configure as configure_logging
from .runtime import mojang, op_config, solana, token_price
from .runtime.client import bundle_path as client_bundle_path
from .runtime.holdings_refresh import refresh_all_holdings
from .store import daily_claims as dc_store
from .store import identity_links as id_store
from .store import servers as srv_store
from .store import wallet_links as wl_store
from .store.db import DEFAULT_DB_PATH, connect
from .web.public_pages import (
    render_landing,
    render_link,
    render_maintenance,
    render_play,
    render_ranks,
)

# Refresh cadences live in op_config (env/default fallback) so an operator can
# retune them live: `snapshot_interval_s` (chain holdings → daily snapshot, the
# metered RPC) and `price_interval_s` (the DexScreener ticker — NOT the RPC; a
# cached decoration, ~4.3k calls/mo at 600s to a free, keyless API). A loop
# re-reads its knob each pass; 0 pauses it.
_PAUSED_RECHECK_S = 60
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


class _ClientApproveReq(_VerifyReq):
    code: str  # the pairing user_code shown by the client


class _JoinVerifyReq(BaseModel):
    token: str  # the join token the ndrchst-auth mod received from the client


class _GateIdentityReq(BaseModel):
    # The Paper plugin's pre-login gate sends the player's real, authenticated
    # identity (online-mode UUID; Bedrock players also carry an xuid).
    uuid: str
    xuid: str | None = None
    username: str | None = None


class _GateLinkStartReq(BaseModel):
    # In-game /link on the Paper path: the plugin hands us the authenticated
    # identity to bind, and we mint a code the player approves with their wallet.
    uuid: str
    xuid: str | None = None
    username: str | None = None


class _GateLinkApproveReq(_VerifyReq):
    code: str  # the /link code shown in-game, approved here with a wallet sig


class _DeviceExchangeReq(BaseModel):
    device_token: str  # the client's long-lived credential


class _HandoffRedeemReq(BaseModel):
    code: str  # one-time handoff code minted by the play page for a deep link


class _DailyClaimReq(BaseModel):
    wallet: str  # the verified wallet the mod stashed at login


class _DailyResetReq(BaseModel):
    wallet: str


class _TierReq(BaseModel):
    wallet: str  # the verified wallet the mod stashed at login


class _OpConfigSetReq(BaseModel):
    key: str
    value: int


class _SkinImportReq(BaseModel):
    texture: str  # 64-hex texture hash from a /me/skin/search result
    model: str = "classic"  # 'slim' or 'classic' — for the in-game arm model


def _cookie_secure() -> bool:
    return os.environ.get("NDRCHST_COOKIE_SECURE", "1") != "0"


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
    """Shared SIWS check used by /auth/verify and /client/auth/approve: the
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


# --- skins (basic per-wallet profile) ----------------------------------------
SKINS_ROOT_DEFAULT = Path.home() / ".ndrchst" / "skins"
_SKIN_DIMS_OK = {(64, 64), (64, 32)}  # modern + legacy Minecraft skin sizes
_SKIN_MAX_BYTES = 256 * 1024


def _skins_dir() -> Path:
    return Path(os.environ.get("NDRCHST_SKINS_DIR", str(SKINS_ROOT_DEFAULT)))


def _skin_path(pubkey: str) -> Path:
    # Wallet pubkeys are base58 (alnum); strip anything else defensively so the
    # filename can never escape the skins dir.
    safe = "".join(c for c in pubkey if c.isalnum())
    return _skins_dir() / f"{safe}.png"


def _skin_meta_path(pubkey: str) -> Path:
    """Sidecar holding the Mojang texture hash + model for an IMPORTED skin, so
    the mod can apply it in-game (offline-mode servers can't fetch skins). Only
    imported skins have this — uploaded custom PNGs have no Mojang-hosted hash."""
    return _skin_path(pubkey).with_suffix(".json")


def _skin_meta(pubkey: str) -> dict | None:
    """{texture, model} for the wallet's in-game skin, or None. Read by the join
    gate so the mod can set the player's texture property at connect time."""
    p = _skin_meta_path(pubkey)
    try:
        m = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    tex = m.get("texture")
    if not isinstance(tex, str) or not mojang.is_texture_hash(tex):
        return None
    model = "slim" if m.get("model") == "slim" else "classic"
    return {"texture": tex, "model": model}


def _png_dims(data: bytes) -> tuple[int, int] | None:
    """(width, height) read straight from a PNG's IHDR — no image lib needed,
    so we can validate a skin upload without a Pillow dependency. None if the
    bytes aren't a PNG."""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return (int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big"))


def _identity(pubkey: str, *, conn: sqlite3.Connection | None = None,
              allow_live: bool = True) -> dict:
    """Public identity view for a wallet: display handle, derived MC name,
    holdings %, rank tier, and (if set) the wallet's skin URL.

    Holdings is served **from the DB**, not a live RPC call: the hourly snapshot
    loop keeps every linked wallet's value fresh, and a wallet's first sign-in
    seeds it. So the high-frequency request paths (/me, the pairing poll loop,
    Play) cost ZERO Solana RPC — only a wallet we've never seen does one live
    read to establish its tier, and `allow_live=False` suppresses even that for
    pure-display endpoints. This is what keeps a metered RPC (Helius, 1M/mo)
    from being drained by edge traffic; freshness for known wallets rides the
    snapshot interval. A live miss still falls back to the last snapshot rather
    than demoting a holder to 0."""
    link = wl_store.get(conn, pubkey) if conn is not None else None
    pct: float | None = None
    if link is not None and link.holdings_pct is not None:
        pct = link.holdings_pct          # DB value: refreshed hourly + at first link
    elif allow_live:
        pct = solana.try_holdings_pct(pubkey)  # never-seen wallet → one live read
    if pct is None and link is not None and link.snapshot_pct is not None:
        pct = link.snapshot_pct          # live miss → last good snapshot, not 0
    if pct is None:
        pct = 0.0
    tier = wallet.tier_for(pct)
    return {
        "wallet": pubkey,
        "display": wallet.abbreviate(pubkey),
        "mc_name": wallet.derive_mc_name(pubkey),
        "holdings_pct": round(pct, 6),
        "tier": tier.key if tier else None,
        "tier_name": tier.name if tier else None,
        "skin_url": f"/skins/{pubkey}.png" if _skin_path(pubkey).exists() else None,
    }


# On a launch/join we want the join token to carry the player's CURRENT tier
# (the "refresh lag" gap: DB-first reads are otherwise only as fresh as the
# hourly loop). So those paths force a live read — but throttled per wallet so a
# crash-relaunch loop, or connect→approve→play in quick succession, can't drain
# the metered RPC. This updates the display/gate value (upsert) only; the hourly
# snapshot stays the carousel-proof basis for /daily rewards (don't write it here
# or a flash-borrow at launch could mint a high-tier daily).
_REFRESH_COOLDOWN = float(os.environ.get("NDRCHST_REFRESH_COOLDOWN", "300"))
_last_refresh: dict[str, float] = {}
_refresh_lock = threading.Lock()


def _refresh_holdings_if_stale(pubkey: str, conn: sqlite3.Connection) -> None:
    """Re-read holdings from chain and persist the gate/display tier, at most
    once per `_REFRESH_COOLDOWN` per wallet. No-op if disabled or within the
    cooldown; a flaky read keeps the existing DB value."""
    if _REFRESH_COOLDOWN > 0:
        now = time.monotonic()
        with _refresh_lock:
            if now - _last_refresh.get(pubkey, 0.0) < _REFRESH_COOLDOWN:
                return
            _last_refresh[pubkey] = now  # claim the slot up front so failures throttle too
    pct = solana.try_holdings_pct(pubkey)
    if pct is None:
        return
    link = wl_store.get(conn, pubkey)
    mc_name = link.mc_name if link is not None else wallet.derive_mc_name(pubkey)
    tier = wallet.tier_for(pct)
    wl_store.upsert(conn, pubkey, mc_name, tier.key if tier else None, pct)


def create_public_app(*, db_path: Path | None = None) -> FastAPI:
    """Factory. The public app keeps its own SQLite connection (read-only)."""
    conn_holder: dict[str, sqlite3.Connection] = {}
    _db_path = db_path or DEFAULT_DB_PATH
    _log = logging.getLogger("ndrchst.public")

    async def _snapshot_loop() -> None:
        """Re-read every linked wallet's chain holdings on a fixed cadence and
        persist the hourly snapshot daily rewards read from. Sleeps BEFORE the
        first run (a launch flash-borrow must not mint a snapshot). Cadence is
        re-read each pass (op-tunable); 0 pauses. Runs on its own connection (the
        blocking RPC + writes go through to_thread so the event loop isn't stalled)."""
        while True:
            interval = op_config.get("snapshot_interval_s")
            if interval <= 0:
                await asyncio.sleep(_PAUSED_RECHECK_S)
                continue
            await asyncio.sleep(interval)
            try:
                n = await asyncio.to_thread(_run_snapshot, _db_path)
                _log.info("holdings snapshot: refreshed %d wallet(s)", n)
            except Exception:
                # A flaky RPC or transient DB error must not kill the loop.
                _log.exception("holdings snapshot failed")

    async def _price_loop() -> None:
        """Refresh the cached $NDRCHST ticker on a slow cadence. Best-effort and
        off the metered RPC (DexScreener); refresh FIRST so the ticker shows soon
        after boot, then sleep. The cadence is re-read each pass (op-tunable); 0
        pauses. The blocking HTTP goes through to_thread."""
        while True:
            interval = op_config.get("price_interval_s")
            if interval <= 0:
                await asyncio.sleep(_PAUSED_RECHECK_S)
                continue
            try:
                await asyncio.to_thread(token_price.refresh)
            except Exception:
                _log.exception("price refresh failed")  # never kills the loop
            await asyncio.sleep(interval)

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
        # Start a loop unless its knob is 0 at boot; a paused loop also self-idles
        # if it's later set to 0 live, so this gate is just to avoid a dead task.
        task = (asyncio.create_task(_snapshot_loop())
                if op_config.get("snapshot_interval_s") > 0 else None)
        price_task = (asyncio.create_task(_price_loop())
                      if op_config.get("price_interval_s") > 0 else None)
        try:
            yield
        finally:
            for t in (task, price_task):
                if t is not None:
                    t.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await t
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
        """Attach a short-lived join token to an identity for the client to
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
        _refresh_holdings_if_stale(req.pubkey, _conn())
        ident = _identity(req.pubkey, conn=_conn())
        _record_link(ident)
        token = auth_session.sign_session(req.pubkey)
        resp = JSONResponse(ident, headers=_NO_STORE)
        resp.set_cookie(
            _SESSION_COOKIE, token, max_age=7 * 24 * 3600, httponly=True,
            samesite="lax", secure=_cookie_secure(), path="/",
        )
        return resp

    # --- desktop client pairing (OAuth device-flow shape) ---------------------
    @app.post("/client/auth/start")
    def client_auth_start() -> JSONResponse:
        """The client begins a pairing; it opens verify_url in a browser and
        polls with pair_id until a wallet is bound."""
        pair_id, user_code = client_pairing.start()
        return JSONResponse({
            "pair_id": pair_id,
            "user_code": user_code,
            "verify_url": f"{_site_base()}/link?code={user_code}",
            "interval": 2,
            "expires_in": 600,
        }, headers=_NO_STORE)

    @app.get("/link", response_class=HTMLResponse)
    def link_page(code: str = "", m: str = "") -> HTMLResponse:
        """Web page where the user connects a wallet to approve a code. `m=g`
        switches it to the in-game /link (Paper) flow → /gate/link/approve."""
        return HTMLResponse(render_link(code=code, mode=m))

    @app.post("/client/auth/approve")
    def client_auth_approve(req: _ClientApproveReq = Body(...)) -> JSONResponse:
        """Verify the wallet signature and bind it to the pairing code."""
        _verify_signed_or_raise(req.pubkey, req.message, req.signature)
        if not client_pairing.approve(req.code, req.pubkey):
            raise HTTPException(status_code=404, detail="unknown or expired pairing code")
        _refresh_holdings_if_stale(req.pubkey, _conn())
        ident = _identity(req.pubkey, conn=_conn())
        _record_link(ident)
        return JSONResponse({"ok": True, **_with_join_token(ident)}, headers=_NO_STORE)

    @app.get("/client/auth/poll")
    def client_auth_poll(pair_id: str) -> JSONResponse:
        """The client polls until its pairing is approved."""
        p = client_pairing.poll(pair_id)
        if p is None:
            raise HTTPException(status_code=404, detail="unknown or expired pairing")
        if not p.pubkey:
            return JSONResponse({"status": "pending"}, headers=_NO_STORE)
        # Polled in a loop while pairing — the wallet was just written by
        # /client/auth/approve, so read its tier from the DB (no live RPC).
        ident = _with_join_token(_identity(p.pubkey, conn=_conn(), allow_live=False))
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
                # Imported skin (Mojang hash + model) so the mod can apply it
                # in-game; null for uploaded/no skin (offline mode can't fetch).
                "skin": _skin_meta(claims["wallet"]),
            },
            headers=_NO_STORE,
        )

    @app.post("/gate/identity")
    def gate_identity(request: Request, req: _GateIdentityReq = Body(...)) -> JSONResponse:
        """Paper / cross-play pre-login gate: map a real MC identity to its
        linked wallet and return the rank. `{ok: false}` (not an error) means
        unlinked — the plugin kicks with a "link at ndrchst.com" prompt.
        Bridge-gated like /join/verify; the Paper server's plugin is the only
        caller. Identity is Mojang-/Floodgate-authenticated upstream (online
        mode), so the UUID/xuid is trustworthy without a signed token."""
        if not _is_internal_caller(request):
            raise HTTPException(status_code=403, detail="not an internal caller")
        link = id_store.get(_conn(), req.uuid)
        if link is None and req.xuid:
            link = id_store.get_by_xuid(_conn(), req.xuid)
        if link is None:
            return JSONResponse({"ok": False, "reason": "unlinked"}, headers=_NO_STORE)
        wl = wl_store.get(_conn(), link.wallet)
        tier_key = wl.tier if (wl and wl.tier) else "holder"
        return JSONResponse(
            {"ok": True, "wallet": link.wallet, "tier": tier_key,
             "skin": _skin_meta(link.wallet)},
            headers=_NO_STORE,
        )

    @app.post("/gate/link/start")
    def gate_link_start(request: Request, req: _GateLinkStartReq = Body(...)) -> JSONResponse:
        """In-game /link (Paper): mint a pairing code bound to the player's
        authenticated identity. Bridge-gated; the plugin shows verify_url to the
        player and polls pair_id until they approve."""
        if not _is_internal_caller(request):
            raise HTTPException(status_code=403, detail="not an internal caller")
        identity = {"uuid": req.uuid, "xuid": req.xuid, "username": req.username}
        pair_id, code = identity_pairing.start(identity)
        return JSONResponse({
            "pair_id": pair_id,
            "user_code": code,
            "verify_url": f"{_site_base()}/link?code={code}&m=g",
            "interval": 2,
            "expires_in": 600,
        }, headers=_NO_STORE)

    @app.post("/gate/link/approve")
    def gate_link_approve(req: _GateLinkApproveReq = Body(...)) -> JSONResponse:
        """Web side of the /link flow: the player approves the in-game code by
        signing with their wallet, which persists the identity → wallet binding.
        Browser-reached (NOT bridge-gated) but gated by the wallet signature,
        exactly like /client/auth/approve."""
        _verify_signed_or_raise(req.pubkey, req.message, req.signature)
        identity = identity_pairing.approve(req.code, req.pubkey)
        if identity is None:
            raise HTTPException(status_code=404, detail="unknown or expired link code")
        _refresh_holdings_if_stale(req.pubkey, _conn())
        id_store.upsert(_conn(), identity["uuid"], req.pubkey,
                        xuid=identity.get("xuid"), username=identity.get("username"))
        ident = _identity(req.pubkey, conn=_conn())
        _record_link(ident)
        return JSONResponse({"ok": True, "tier": ident.get("tier") or "holder"},
                            headers=_NO_STORE)

    @app.get("/gate/link/poll")
    def gate_link_poll(request: Request, pair_id: str) -> JSONResponse:
        """The plugin polls until the player approves, then re-gates them live
        (no rejoin needed). Bridge-gated."""
        if not _is_internal_caller(request):
            raise HTTPException(status_code=403, detail="not an internal caller")
        p = identity_pairing.poll(pair_id)
        if p is None:
            raise HTTPException(status_code=404, detail="unknown or expired link")
        if not p.pubkey:
            return JSONResponse({"status": "pending"}, headers=_NO_STORE)
        wl = wl_store.get(_conn(), p.pubkey)
        tier_key = wl.tier if (wl and wl.tier) else "holder"
        return JSONResponse({"status": "approved", "wallet": p.pubkey, "tier": tier_key},
                            headers=_NO_STORE)

    @app.post("/device/exchange")
    def device_exchange(req: _DeviceExchangeReq = Body(...)) -> JSONResponse:
        """The client trades its long-lived device token for a FRESH short-lived
        join token (tier re-read from chain) at Play — so the gate credential
        is never stale and there's no in-launcher device-flow round-trip."""
        wallet_pk = device_token.verify(req.device_token)
        if not wallet_pk:
            raise HTTPException(status_code=401, detail="invalid or expired device token")
        # Re-read tier from chain at Play (throttled) so the join token is current.
        _refresh_holdings_if_stale(wallet_pk, _conn())
        ident = _identity(wallet_pk, conn=_conn())
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
            ok, seconds_left = dc_store.try_claim(
                conn, req.wallet, cooldown_s=op_config.get("daily_cooldown_s"))
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

    @app.post("/tier")
    def tier_lookup(request: Request, req: _TierReq = Body(...)) -> JSONResponse:
        """Called by the mod's `/tier`: the player's standing for an in-game
        readout — current tier + holdings % + the next threshold to climb. Reads
        the latest known holdings from the DB (kept fresh by sign-in + the hourly
        snapshot); does NO live RPC, so it can't burn the metered cap. Mod-only
        (bridge): floored to the base tier so a linked wallet always has a tier."""
        if not _is_internal_caller(request):
            raise HTTPException(status_code=403, detail="not an internal caller")
        link = wl_store.get(_conn(), req.wallet)
        pct = link.holdings_pct if (link and link.holdings_pct is not None) else 0.0
        cur = wallet.tier_for(pct) or wallet.DEFAULT_TIERS[0]
        nxt = wallet.next_tier(pct)
        price = token_price.get()  # cached decoration; None until first refresh
        return JSONResponse(
            {
                "ok": True,
                "wallet": req.wallet,
                "pct": round(pct, 6),
                "tier": cur.key,
                "tier_name": cur.name,
                "next": (
                    {"key": nxt.key, "name": nxt.name, "min_pct": nxt.min_pct}
                    if nxt
                    else None
                ),
                "price": (
                    {"usd": price["price_usd"], "market_cap": price["market_cap"]}
                    if price
                    else None
                ),
            },
            headers=_NO_STORE,
        )

    @app.post("/ops/config")
    def ops_config(request: Request) -> JSONResponse:
        """List the operator-tunable runtime knobs + their effective values (for
        the in-game `/ndrchst config`). Mod-only (bridge); read-only."""
        if not _is_internal_caller(request):
            raise HTTPException(status_code=403, detail="not an internal caller")
        return JSONResponse(
            {"ok": True, "config": op_config.all_values()}, headers=_NO_STORE)

    @app.post("/ops/config/set")
    def ops_config_set(request: Request, req: _OpConfigSetReq = Body(...)) -> JSONResponse:
        """Set a runtime knob live (`/ndrchst config <key> <value>`): the daily
        cooldown or a refresh cadence. Persists across restart and clamps to the
        knob's floor. Mod-only (bridge) — and the mod gates the command to ops."""
        if not _is_internal_caller(request):
            raise HTTPException(status_code=403, detail="not an internal caller")
        try:
            applied = op_config.set(req.key, req.value)
        except KeyError:
            raise HTTPException(
                status_code=400, detail=f"unknown knob: {req.key}") from None
        return JSONResponse(
            {"ok": True, "key": req.key, "value": applied,
             "config": op_config.all_values()},
            headers=_NO_STORE,
        )

    @app.get("/price")
    def price_lookup(request: Request) -> JSONResponse:
        """The cached $NDRCHST ticker for the in-game tab menu (DexScreener cache,
        0 RPC). Mod-only (bridge) — public market data, but no need for a new
        public surface since the website renders it server-side."""
        if not _is_internal_caller(request):
            raise HTTPException(status_code=403, detail="not an internal caller")
        p = token_price.get()
        return JSONResponse(
            {"ok": True,
             "price": ({"usd": p["price_usd"], "market_cap": p["market_cap"]}
                       if p else None)},
            headers=_NO_STORE,
        )

    @app.post("/me/handoff")
    def me_handoff(request: Request) -> JSONResponse:
        """Mint a one-time handoff code for the signed-in wallet. The play page
        embeds it in a ``ndrchst://`` deep link so an already-installed client
        can link itself without a second sign-in. Requires a wallet session."""
        pubkey = auth_session.verify_session(request.cookies.get(_SESSION_COOKIE))
        if not pubkey:
            raise HTTPException(status_code=401, detail="not signed in")
        return JSONResponse({"code": client_handoff.mint(pubkey)}, headers=_NO_STORE)

    @app.post("/client/auth/handoff")
    def client_auth_handoff(req: _HandoffRedeemReq = Body(...)) -> JSONResponse:
        """Redeem a one-time handoff code (carried in a ``ndrchst://`` deep link)
        for a durable device token + a fresh identity. The code — not a
        credential — is what travelled in the URL; it's single-use and
        short-lived, so a leaked link can't be replayed."""
        wallet_pk = client_handoff.redeem(req.code)
        if not wallet_pk:
            raise HTTPException(status_code=404, detail="invalid or expired handoff code")
        _refresh_holdings_if_stale(wallet_pk, _conn())
        ident = _identity(wallet_pk, conn=_conn())
        _record_link(ident)
        return JSONResponse(
            {**_with_join_token(ident), "device_token": device_token.issue(wallet_pk)},
            headers=_NO_STORE,
        )

    @app.get("/me/client/{server_id}")
    def me_client(server_id: str, request: Request) -> Response:
        """Authenticated, personalized client download: requires a wallet session
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
        base = client_bundle_path(server_id)
        if base is None:
            raise HTTPException(status_code=404, detail="client bundle not built for this server")
        buf = io.BytesIO()
        with zipfile.ZipFile(base) as src, \
                zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
            for item in src.infolist():
                out.writestr(item, src.read(item.filename))
            out.writestr("ndrchst-device.token", device_token.issue(wallet_pk))
        fname = f"ndrchst-client-{server.name.replace(' ', '_')}.zip"
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
        # /me is hit on every page load — serve tier from the DB, no live RPC.
        return JSONResponse(_identity(pubkey, conn=_conn(), allow_live=False),
                            headers=_NO_STORE)

    @app.post("/auth/logout")
    def auth_logout() -> JSONResponse:
        resp = JSONResponse({"ok": True}, headers=_NO_STORE)
        resp.delete_cookie(_SESSION_COOKIE, path="/")
        return resp

    # --- profile: per-wallet skin -------------------------------------------
    @app.post("/me/skin")
    async def set_skin(request: Request) -> JSONResponse:
        """Store the signed-in wallet's skin (raw PNG body). Validated to a
        64x64 / 64x32 PNG so we never persist arbitrary uploads."""
        pubkey = auth_session.verify_session(request.cookies.get(_SESSION_COOKIE))
        if not pubkey:
            raise HTTPException(status_code=401, detail="not signed in")
        body = await request.body()
        if len(body) > _SKIN_MAX_BYTES:
            raise HTTPException(status_code=413, detail="skin too large (max 256 KB)")
        if _png_dims(body) not in _SKIN_DIMS_OK:
            raise HTTPException(status_code=400, detail="skin must be a 64x64 (or 64x32) PNG")
        path = _skin_path(pubkey)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        # An uploaded PNG isn't Mojang-hosted, so it can't be applied in-game by
        # hash — drop any stale imported-skin sidecar so the gate won't reuse it.
        _skin_meta_path(pubkey).unlink(missing_ok=True)
        return JSONResponse({"ok": True, "skin_url": f"/skins/{pubkey}.png"}, headers=_NO_STORE)

    @app.delete("/me/skin")
    def clear_skin(request: Request) -> JSONResponse:
        pubkey = auth_session.verify_session(request.cookies.get(_SESSION_COOKIE))
        if not pubkey:
            raise HTTPException(status_code=401, detail="not signed in")
        _skin_path(pubkey).unlink(missing_ok=True)
        _skin_meta_path(pubkey).unlink(missing_ok=True)
        return JSONResponse({"ok": True}, headers=_NO_STORE)

    @app.get("/skins/{name}")
    def get_skin(name: str) -> FileResponse:
        """Public skin read (skins are public, like Minecraft's). `name` is the
        wallet pubkey, optionally with a .png suffix."""
        pubkey = name[:-4] if name.endswith(".png") else name
        path = _skin_path(pubkey)
        if not path.exists():
            raise HTTPException(status_code=404, detail="no skin set")
        return FileResponse(path, media_type="image/png",
                            headers={"Cache-Control": "no-cache"})

    @app.get("/me/skin/search")
    def skin_search(request: Request, q: str = "") -> JSONResponse:
        """Find a skin by Minecraft username (Mojang). The Skindex is walled by
        a Cloudflare JS challenge a server can't pass, so search resolves a
        username to that player's skin instead — reliable + dependency-free.
        Returns at most one result: {name, model, texture, preview_url}. Gated
        to a signed-in wallet so it can't be used as an open Mojang proxy."""
        if not auth_session.verify_session(request.cookies.get(_SESSION_COOKIE)):
            raise HTTPException(status_code=401, detail="sign in with your wallet first")
        found = mojang.lookup_skin(q.strip()) if q.strip() else None
        results = [] if found is None else [{
            "name": found["name"],
            "model": found["model"],
            "texture": found["texture"],
            "preview_url": f"/me/skin/preview/{found['texture']}",
        }]
        return JSONResponse({"results": results}, headers=_NO_STORE)

    @app.get("/me/skin/preview/{texture}")
    def skin_preview(texture: str) -> Response:
        """Proxy a Mojang texture so the https page can preview an http://
        textures.minecraft.net skin without mixed-content. Hash-only input ⇒
        the box can only ever fetch textures.minecraft.net/texture/<hash>."""
        data = mojang.fetch_texture(texture)
        if data is None:
            raise HTTPException(status_code=404, detail="texture not found")
        return Response(content=data, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})

    @app.post("/me/skin/import")
    def skin_import(request: Request, req: _SkinImportReq = Body(...)) -> JSONResponse:
        """Apply a searched skin: fetch the texture by hash, validate it as a
        64x64 / 64x32 PNG, and store it under the signed-in wallet — the same
        slot as an uploaded skin."""
        pubkey = auth_session.verify_session(request.cookies.get(_SESSION_COOKIE))
        if not pubkey:
            raise HTTPException(status_code=401, detail="not signed in")
        data = mojang.fetch_texture(req.texture)
        if data is None:
            raise HTTPException(status_code=502, detail="could not fetch that skin")
        if _png_dims(data) not in _SKIN_DIMS_OK:
            raise HTTPException(status_code=400, detail="skin must be a 64x64 (or 64x32) PNG")
        path = _skin_path(pubkey)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        # Record the Mojang hash + model so the mod can apply it in-game.
        model = "slim" if req.model == "slim" else "classic"
        _skin_meta_path(pubkey).write_text(
            json.dumps({"texture": req.texture, "model": model}))
        return JSONResponse({"ok": True, "skin_url": f"/skins/{pubkey}.png"}, headers=_NO_STORE)

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
                "client_url": f"/client/{s.id}/client.zip",
                "config_url": f"/client/{s.id}/config.json",
            }
            for s in srv_store.list_all(_conn())
            if s.family is Family.JAVA
        ]

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        # The whole surface lives on one host now (play.ndrchst.com; apex + www
        # 301 here), so the root is unambiguous: it's the marketing landing. The
        # app is at /play. No more host-sniffing — one host, one meaning.
        return HTMLResponse(render_landing())

    @app.get("/play", response_class=HTMLResponse)
    def play() -> HTMLResponse:
        import os
        downloads_base = os.environ.get("NDRCHST_CLIENT_DOWNLOADS_BASE", "")
        return HTMLResponse(render_play(_play_servers(), downloads_base=downloads_base))

    @app.get("/maintenance", response_class=HTMLResponse)
    def maintenance() -> HTMLResponse:
        # Also published to R2 as maintenance.html; the edge Worker serves that
        # copy when this origin is unreachable. Here for direct access + tests.
        return HTMLResponse(render_maintenance(), status_code=503)

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
        return HTMLResponse(render_ranks(
            holders, tiers, ticker=token_price.get(),
            claim_cooldown_s=op_config.get("daily_cooldown_s")))

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
                "client_url": f"/client/{s.id}/client.zip",
                "config_url": f"/client/{s.id}/config.json",
            })
        return out

    # Client bundles regenerate on demand (POST /servers/{id}/client/regenerate
    # on the admin plane). If a CDN ahead of us caches the old copy, the new
    # config sits unreachable until TTL expiry. `no-store` opts every layer
    # — browsers + Cloudflare — out of caching these per-server files.
    _NO_STORE = {"Cache-Control": "no-store, no-cache, must-revalidate"}

    @app.get("/client/{server_id}/client.zip")
    def download_client(server_id: str) -> FileResponse:
        server = srv_store.get(_conn(), server_id)
        if server is None:
            raise HTTPException(status_code=404, detail="server not found")
        path = client_bundle_path(server_id)
        if path is None:
            raise HTTPException(
                status_code=404,
                detail="client bundle not built yet (Java servers only); recreate the server",
            )
        return FileResponse(
            path,
            media_type="application/zip",
            filename=f"ndrchst-client-{server.name.replace(' ', '_')}.zip",
            headers=_NO_STORE,
        )

    @app.get("/client/{server_id}/config.json")
    def client_config(server_id: str) -> JSONResponse:
        from .runtime.client import CLIENTS_ROOT_DEFAULT
        cfg_path = CLIENTS_ROOT_DEFAULT / server_id / "config.json"
        if not cfg_path.exists():
            raise HTTPException(status_code=404, detail="config not found")
        import json
        return JSONResponse(json.loads(cfg_path.read_text()), headers=_NO_STORE)

    @app.get("/client/{server_id}/manifest.json")
    def client_manifest(server_id: str) -> JSONResponse:
        from .runtime.client import CLIENTS_ROOT_DEFAULT
        mp = CLIENTS_ROOT_DEFAULT / server_id / "manifest.json"
        if not mp.exists():
            raise HTTPException(status_code=404, detail="manifest not found")
        import json
        return JSONResponse(json.loads(mp.read_text()), headers=_NO_STORE)

    @app.get("/client/{server_id}/modpack.zip")
    def client_modpack(server_id: str) -> FileResponse:
        """Per-server modpack zip companion to client.zip — staged when
        the operator wants the client to install a CF client pack that CF's
        own CDN won't serve directly (which is most client packs).

        Used for the overrides/* tree (configs, kubejs, defaultconfigs);
        the actual mod jars come from the /mods/ endpoints below so the
        client mirrors the server's curated set, not whatever the upstream
        CF manifest happens to point at."""
        from .runtime.client import CLIENTS_ROOT_DEFAULT
        path = CLIENTS_ROOT_DEFAULT / server_id / "modpack.zip"
        if not path.exists():
            raise HTTPException(status_code=404, detail="modpack not staged for this server")
        return FileResponse(
            path,
            media_type="application/zip",
            filename="modpack.zip",
            headers=_NO_STORE,
        )

    @app.get("/client/{server_id}/mods/index.json")
    def client_mods_index(server_id: str) -> JSONResponse:
        """The mod set the client should mirror. Prefers the cached
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
            origin = f"/client/{server_id}/mods/{urllib.parse.quote(p.name, safe='')}"
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

    @app.get("/client/{server_id}/mods/{filename}")
    def client_mod_file(server_id: str, filename: str) -> FileResponse:
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

    @app.get("/client/{server_id}/mods.zip")
    def client_mods_bundle(server_id: str) -> FileResponse:
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
