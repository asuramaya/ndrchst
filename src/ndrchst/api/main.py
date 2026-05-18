from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import __version__
from . import mods, platforms, servers

WEB = Path(__file__).resolve().parent.parent / "web"
templates = Jinja2Templates(directory=str(WEB / "templates"))


def create_app() -> FastAPI:
    app = FastAPI(title="ndrchst", version=__version__)

    app.mount("/static", StaticFiles(directory=str(WEB / "static")), name="static")

    app.include_router(servers.router, prefix="/api/servers", tags=["servers"])
    app.include_router(platforms.router, prefix="/api/platforms", tags=["platforms"])
    app.include_router(mods.router, prefix="/api/mods", tags=["mods"])

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "index.html")

    return app


app = create_app()
