from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..runtime.lifecycle import SERVERS_ROOT_DEFAULT
from ..store.db import DEFAULT_DB_PATH
from ..web import routes as web_routes
from . import mods, platforms, servers
from .deps import make_lifespan, state

WEB = Path(__file__).resolve().parent.parent / "web"


def create_app(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    servers_root: Path = SERVERS_ROOT_DEFAULT,
) -> FastAPI:
    app = FastAPI(
        title="ndrchst",
        version=__version__,
        lifespan=make_lifespan(db_path=db_path, servers_root=servers_root),
    )

    app.mount("/static", StaticFiles(directory=str(WEB / "static")), name="static")

    # JSON API
    app.include_router(servers.router, prefix="/api/servers", tags=["servers"])
    app.include_router(platforms.router, prefix="/api/platforms", tags=["platforms"])
    app.include_router(mods.router, prefix="/api/mods", tags=["mods"])

    # HTML (htmx-driven UI)
    app.include_router(web_routes.router)

    @app.get("/healthz")
    def healthz(request: Request) -> dict:
        s = state(request)
        return {
            "status": "ok",
            "version": __version__,
            "docker": "ok" if s.lifecycle is not None else "unavailable",
            "docker_error": s.docker_error,
        }

    return app


app = create_app()
