from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException

from ..platforms import REGISTRY

router = APIRouter()


@router.get("")
def list_platforms(include_hidden: bool = False) -> list[dict]:
    """List registered platforms. By default hides platforms marked
    ``default_visible=False`` (e.g. Bedrock while the product is focused on
    modded Java). Pass ``?include_hidden=true`` to see them anyway — useful
    for debugging and for any future "advanced" surface."""
    return [
        {
            "id": p.id,
            "family": p.family.value,
            "name": p.display_name,
            "implemented": p.implemented,
            "default_visible": getattr(p, "default_visible", True),
            "default_memory_mb": getattr(p, "default_memory_mb", 2048),
        }
        for p in REGISTRY.values()
        if include_hidden or getattr(p, "default_visible", True)
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
