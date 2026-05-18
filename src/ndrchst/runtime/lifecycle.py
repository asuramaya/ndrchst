"""Server lifecycle: compose platform install + docker container + rcon.

This is the only place that knows about all four subsystems
(platforms, runtime/docker, store, eventually runtime/geyser). Everything
else stays oblivious.
"""
from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..domain.models import Family, Server, ServerStatus
from ..platforms import REGISTRY as PLATFORMS
from ..store import servers as srv_store
from .docker import BEDROCK_IMAGE, ContainerSpec, Docker, java_image_for
from .geyser import install_cross_play

SERVERS_ROOT_DEFAULT = Path.home() / ".ndrchst" / "servers"

_NAME_OK = re.compile(r"^[A-Za-z0-9 _-]{1,64}$")
_PORT_RANGE = range(1024, 65536)


class LifecycleError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class CreateRequest:
    name: str
    platform_id: str
    version: str
    port: int
    memory_mb: int = 2048
    cross_play: bool = False  # Java-only flag; Bedrock is already bedrock


def _new_server_id() -> str:
    # 12 hex chars — enough for collision-resistance under our scale
    return uuid.uuid4().hex[:12]


def _container_name(server_id: str) -> str:
    return f"ndrchst-{server_id}"


def _build_spec(server: Server, data_dir: Path) -> ContainerSpec:
    if server.family is Family.JAVA:
        image = java_image_for(server.version)
        xmx = server.memory_mb
        xms = max(server.memory_mb // 2, 512)
        cmd = ["java", f"-Xms{xms}m", f"-Xmx{xmx}m", "-jar", "server.jar", "nogui"]
        env = {"EULA": "TRUE"}
        ports = {25565: server.port}
    elif server.family is Family.BEDROCK:
        image = BEDROCK_IMAGE
        # BDS needs LD_LIBRARY_PATH because its libs live alongside the binary
        cmd = ["./bedrock_server"]
        env = {"LD_LIBRARY_PATH": "."}
        ports = {19132: server.port}
    else:
        raise LifecycleError(f"unsupported family: {server.family}")

    return ContainerSpec(
        name=_container_name(server.id),
        image=image,
        cmd=cmd,
        workdir="/data",
        data_dir=data_dir,
        ports=ports,
        memory_mb=server.memory_mb,
        server_id=server.id,
        family=server.family,
        env=env,
    )


def _validate(req: CreateRequest, conn: sqlite3.Connection) -> None:
    if not _NAME_OK.match(req.name):
        raise LifecycleError(
            f"server name must be 1-64 chars of [A-Za-z0-9 _-], got: {req.name!r}"
        )
    if req.platform_id not in PLATFORMS:
        raise LifecycleError(f"unknown platform: {req.platform_id}")
    if req.port not in _PORT_RANGE:
        raise LifecycleError(f"port must be 1024-65535, got {req.port}")
    if req.memory_mb < 512:
        raise LifecycleError(f"memory must be >= 512 MB, got {req.memory_mb}")
    if srv_store.port_in_use(conn, req.port):
        raise LifecycleError(f"port {req.port} already used by another server")


class Lifecycle:
    """Composes the subsystems. Callers inject Docker + conn for testability."""

    def __init__(
        self,
        docker: Docker,
        conn: sqlite3.Connection,
        *,
        servers_root: Path = SERVERS_ROOT_DEFAULT,
    ):
        self._docker = docker
        self._conn = conn
        self._root = servers_root

    async def create(self, req: CreateRequest) -> Server:
        _validate(req, self._conn)

        platform = PLATFORMS[req.platform_id]
        server_id = _new_server_id()
        data_dir = self._root / server_id

        # Install the platform binary to the data dir. This is the only
        # step that touches upstream APIs and disk before we record state.
        await platform.install(req.version, data_dir)

        # Cross-play is Java-only. Bedrock servers ARE bedrock.
        if req.cross_play:
            if platform.family is not Family.JAVA:
                raise LifecycleError("cross_play is only meaningful for Java servers")
            await install_cross_play(data_dir, java_port=req.port)

        server = Server(
            id=server_id,
            name=req.name,
            platform_id=req.platform_id,
            family=platform.family,
            version=req.version,
            port=req.port,
            memory_mb=req.memory_mb,
            cross_play=req.cross_play,
        )

        spec = _build_spec(server, data_dir)
        container_id = await self._docker.create_container(spec)
        server.container_id = container_id

        srv_store.insert(self._conn, server)
        return server

    async def start(self, server_id: str) -> None:
        server = self._must_get(server_id)
        if server.container_id is None:
            raise LifecycleError(f"server {server_id} has no container")
        srv_store.update_status(self._conn, server_id, ServerStatus.STARTING)
        await self._docker.start(server.container_id)
        srv_store.update_status(self._conn, server_id, ServerStatus.RUNNING)

    async def stop(self, server_id: str, *, timeout: int = 30) -> None:
        server = self._must_get(server_id)
        if server.container_id is None:
            return
        srv_store.update_status(self._conn, server_id, ServerStatus.STOPPING)
        await self._docker.stop(server.container_id, timeout=timeout)
        srv_store.update_status(self._conn, server_id, ServerStatus.STOPPED)

    async def restart(self, server_id: str, *, timeout: int = 30) -> None:
        server = self._must_get(server_id)
        if server.container_id is None:
            raise LifecycleError(f"server {server_id} has no container")
        await self._docker.restart(server.container_id, timeout=timeout)

    async def delete(self, server_id: str, *, remove_files: bool = False) -> None:
        server = self._must_get(server_id)
        if server.container_id is not None:
            await self._docker.remove(server.container_id, force=True)
        if remove_files:
            data_dir = self._root / server_id
            if data_dir.exists():
                import shutil
                shutil.rmtree(data_dir, ignore_errors=True)
        srv_store.delete(self._conn, server_id)

    async def logs(self, server_id: str, *, lines: int = 100) -> str:
        server = self._must_get(server_id)
        if server.container_id is None:
            return ""
        return await self._docker.logs(server.container_id, lines=lines)

    def _must_get(self, server_id: str) -> Server:
        s = srv_store.get(self._conn, server_id)
        if s is None:
            raise LifecycleError(f"server {server_id!r} not found")
        return s
