"""JSON API for /api/servers."""
from __future__ import annotations

import sqlite3
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..runtime.lifecycle import CreateRequest, Lifecycle, LifecycleError
from ..store import servers as srv_store
from .deps import db, require_lifecycle

router = APIRouter()


class CreateServerBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    platform_id: str
    version: str = "latest"
    port: int = Field(ge=1024, le=65535)
    memory_mb: int = Field(ge=512, le=65536, default=2048)
    cross_play: bool = False
    bedrock_bridge_port: int = Field(ge=1024, le=65535, default=19132)


def _serialize(server) -> dict:
    d = asdict(server)
    d["family"] = server.family.value
    d["status"] = server.status.value
    d["created_at"] = server.created_at.isoformat()
    return d


@router.get("")
def list_servers(conn: sqlite3.Connection = Depends(db)) -> list[dict]:
    return [_serialize(s) for s in srv_store.list_all(conn)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_server(
    body: CreateServerBody,
    lifecycle: Lifecycle = Depends(require_lifecycle),
) -> dict:
    try:
        server = await lifecycle.create(CreateRequest(**body.model_dump()))
    except LifecycleError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _serialize(server)


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(
    server_id: str,
    remove_files: bool = False,
    lifecycle: Lifecycle = Depends(require_lifecycle),
) -> None:
    try:
        await lifecycle.delete(server_id, remove_files=remove_files)
    except LifecycleError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/{server_id}/start", status_code=status.HTTP_202_ACCEPTED)
async def start_server(
    server_id: str,
    lifecycle: Lifecycle = Depends(require_lifecycle),
) -> dict:
    try:
        await lifecycle.start(server_id)
    except LifecycleError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"status": "starting"}


@router.post("/{server_id}/stop", status_code=status.HTTP_202_ACCEPTED)
async def stop_server(
    server_id: str,
    lifecycle: Lifecycle = Depends(require_lifecycle),
) -> dict:
    try:
        await lifecycle.stop(server_id)
    except LifecycleError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"status": "stopping"}
