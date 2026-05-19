from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException

from ..platforms import REGISTRY

router = APIRouter()


@router.get("")
def list_platforms() -> list[dict]:
    return [
        {
            "id": p.id,
            "family": p.family.value,
            "name": p.display_name,
            "implemented": p.implemented,
        }
        for p in REGISTRY.values()
    ]


@router.get("/{platform_id}/versions")
async def platform_versions(platform_id: str, limit: int = 50) -> list[dict]:
    """Live version list from the platform. Cached implicitly by the OS;
    upstream APIs are CDN-fronted so this is cheap. The first entry is the
    newest — what the create form uses as the "latest" hint.
    """
    if platform_id not in REGISTRY:
        raise HTTPException(status_code=404, detail=f"unknown platform: {platform_id}")
    platform = REGISTRY[platform_id]
    if not platform.implemented:
        raise HTTPException(
            status_code=400,
            detail=f"platform '{platform_id}' is not yet implemented",
        )
    try:
        versions = await platform.versions()
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=502,
            detail=f"failed to fetch versions from {platform_id}: {e}",
        ) from e
    return [
        {"version": v.version, "stable": v.stable, "build": v.build}
        for v in versions[:limit]
    ]
