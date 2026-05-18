from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("")
def list_servers() -> list[dict]:
    return []
