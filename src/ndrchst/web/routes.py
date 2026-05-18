"""HTML routes (htmx-driven UI).

Each feature owns a route module under web/. v0 only has servers; everything
else is a "coming soon" placeholder page so the sidebar nav doesn't dead-end.

Routes here distinguish full-page vs htmx-fragment responses by checking the
HX-Request header. The same URL serves both, simplifying refresh/back-button
behaviour.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..api.deps import state
from .servers_routes import router as servers_router

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))

router = APIRouter()
router.include_router(servers_router)


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


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
