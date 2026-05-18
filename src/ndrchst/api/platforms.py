from __future__ import annotations

from fastapi import APIRouter

from ..platforms import REGISTRY

router = APIRouter()


@router.get("")
def list_platforms() -> list[dict]:
    return [
        {"id": p.id, "family": p.family.value, "name": p.display_name}
        for p in REGISTRY.values()
    ]
