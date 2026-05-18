from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/sources")
def list_sources() -> list[dict]:
    from ..mods import REGISTRY
    return [{"id": s.id, "name": s.display_name} for s in REGISTRY.values()]
