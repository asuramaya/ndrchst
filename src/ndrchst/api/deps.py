"""FastAPI dependencies: app-scoped Lifecycle + Docker + DB connection.

Boot is Docker-optional: if `docker.from_env().ping()` fails, the app still
serves the read-only surface (list, healthz, UI shell) but mutation routes
return 503 via `require_lifecycle()`. This makes `uv run ndrchst run` viable
on machines without Docker for UX work and CI.
"""
from __future__ import annotations

import logging
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import docker
from fastapi import FastAPI, HTTPException, Request, status

from ..runtime.docker import Docker
from ..runtime.lifecycle import SERVERS_ROOT_DEFAULT, Lifecycle
from ..store.db import DEFAULT_DB_PATH, connect

log = logging.getLogger("ndrchst")


@dataclass
class AppState:
    """What `request.app.state.ndrchst` holds."""
    conn: sqlite3.Connection
    lifecycle: Lifecycle | None  # None when Docker is unreachable
    docker_error: str | None     # populated if lifecycle is None


def make_lifespan(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    servers_root: Path = SERVERS_ROOT_DEFAULT,
):
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        conn = connect(db_path)
        lifecycle: Lifecycle | None = None
        docker_error: str | None = None
        try:
            client = docker.from_env()
            client.ping()
            lifecycle = Lifecycle(Docker(client=client), conn, servers_root=servers_root)
            log.info("Docker reachable; lifecycle active")
        except Exception as e:
            docker_error = f"{type(e).__name__}: {e}"
            log.warning("Docker unreachable (%s); running in read-only mode", docker_error)

        app.state.ndrchst = AppState(conn=conn, lifecycle=lifecycle, docker_error=docker_error)
        try:
            yield
        finally:
            conn.close()
    return lifespan


def state(request: Request) -> AppState:
    return request.app.state.ndrchst


def require_lifecycle(request: Request) -> Lifecycle:
    s = state(request)
    if s.lifecycle is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Docker is not available: {s.docker_error}",
        )
    return s.lifecycle


def db(request: Request) -> sqlite3.Connection:
    return state(request).conn
