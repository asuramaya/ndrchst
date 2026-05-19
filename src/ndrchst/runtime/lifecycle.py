"""Server lifecycle: compose platform install + docker container + rcon.

This is the only place that knows about all four subsystems
(platforms, runtime/docker, store, eventually runtime/geyser). Everything
else stays oblivious.
"""
from __future__ import annotations

import re
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..domain.models import Family, Server, ServerStatus
from ..platforms import REGISTRY as PLATFORMS
from ..store import servers as srv_store
from .docker import BEDROCK_IMAGE, ContainerSpec, Docker, java_image_for
from .eula import accept_eula
from .geyser import install_cross_play
from .pilot import PilotBuildError
from .pilot import build_bundle as build_pilot_bundle
from .pilot import remove_bundle as remove_pilot_bundle
from .ports import is_port_free

SERVERS_ROOT_DEFAULT = Path.home() / ".ndrchst" / "servers"

_NAME_OK = re.compile(r"^[A-Za-z0-9 _-]{1,64}$")
_PORT_RANGE = range(1024, 65536)


class LifecycleError(Exception):
    pass


DEFAULT_BEDROCK_BRIDGE_PORT = 19132


@dataclass(frozen=True, slots=True)
class CreateRequest:
    name: str
    platform_id: str
    version: str
    port: int
    memory_mb: int = 2048
    cross_play: bool = False  # Java-only flag; Bedrock is already bedrock
    # Geyser UDP listener port on the host. Only used when cross_play=True
    # for Java servers. Default 19132 (the Mojang Bedrock convention).
    bedrock_bridge_port: int = DEFAULT_BEDROCK_BRIDGE_PORT


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
        ports: dict[str, int] = {"25565/tcp": server.port}
        # Cross-play servers also need Geyser's UDP listener exposed so
        # Bedrock clients can reach it from outside the container.
        if server.cross_play and server.bedrock_bridge_port is not None:
            ports["19132/udp"] = server.bedrock_bridge_port
    elif server.family is Family.BEDROCK:
        image = BEDROCK_IMAGE
        # BDS needs LD_LIBRARY_PATH because its libs live alongside the binary.
        # BDS 1.21+ links against libcurl4 which is NOT in ubuntu:24.04 by
        # default — we install it on first boot. apt-get install is idempotent
        # and fast (~1s) on subsequent boots after the package is cached.
        cmd = [
            "sh", "-c",
            "command -v curl >/dev/null 2>&1 || "
            "(apt-get update -qq && apt-get install -y -qq libcurl4 >/dev/null) "
            "&& exec ./bedrock_server",
        ]
        env = {"LD_LIBRARY_PATH": "."}
        ports = {"19132/udp": server.port}
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


def _validate(req: CreateRequest, conn: sqlite3.Connection, *, check_host_port: bool = True) -> None:
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

    platform_family = PLATFORMS[req.platform_id].family
    if check_host_port and not is_port_free(req.port, platform_family):
        proto = "UDP" if platform_family.value == "bedrock" else "TCP"
        raise LifecycleError(
            f"port {req.port}/{proto} is already bound by another process on the host"
        )

    if req.cross_play:
        if platform_family is not Family.JAVA:
            raise LifecycleError("cross_play is only meaningful for Java servers")
        if req.bedrock_bridge_port not in _PORT_RANGE:
            raise LifecycleError(
                f"bedrock_bridge_port must be 1024-65535, got {req.bedrock_bridge_port}"
            )
        if req.bedrock_bridge_port == req.port:
            raise LifecycleError(
                "bedrock_bridge_port must differ from the Java port"
            )
        if srv_store.port_in_use(conn, req.bedrock_bridge_port):
            raise LifecycleError(
                f"bedrock_bridge_port {req.bedrock_bridge_port} already used by another server"
            )
        if check_host_port and not is_port_free(req.bedrock_bridge_port, Family.BEDROCK):
            raise LifecycleError(
                f"bedrock_bridge_port {req.bedrock_bridge_port}/UDP is already bound on the host"
            )


class Lifecycle:
    """Composes the subsystems. Callers inject Docker + conn for testability."""

    def __init__(
        self,
        docker: Docker,
        conn: sqlite3.Connection,
        *,
        servers_root: Path = SERVERS_ROOT_DEFAULT,
        public_host: str | None = None,
    ):
        self._docker = docker
        self._conn = conn
        self._root = servers_root
        # Address the pilot client should dial. Lifespan-time decision: env
        # var (`NDRCHST_PUBLIC_HOST`) or the Tailscale IP; falls back to a
        # placeholder users have to edit by hand.
        self._public_host = public_host or ""

    async def create(self, req: CreateRequest) -> Server:
        _validate(req, self._conn)

        platform = PLATFORMS[req.platform_id]
        server_id = _new_server_id()
        data_dir = self._root / server_id

        # Resolve "latest" magic-string to the platform's actual current
        # version so the Server record stores a concrete pin (good for the
        # UI, easier to debug, makes Geyser version-matching deterministic).
        version = req.version
        if version == "latest":
            try:
                versions_list = await platform.versions()
            except NotImplementedError as e:
                raise LifecycleError(
                    f"platform '{req.platform_id}' is not yet implemented; "
                    f"choose paper or bedrock"
                ) from e
            except (httpx.HTTPError, ValueError, RuntimeError) as e:
                raise LifecycleError(
                    f"failed to resolve latest version for {req.platform_id}: {e}"
                ) from e
            if not versions_list:
                raise LifecycleError(f"no versions available for {req.platform_id}")
            version = versions_list[0].version

        # Install the platform binary to the data dir. This is the only
        # step that touches upstream APIs and disk before we record state.
        # Wrap upstream + stub errors in LifecycleError so the API/web
        # layers can surface a clean 4xx instead of bubbling a 500.
        try:
            await platform.install(version, data_dir)
        except NotImplementedError as e:
            shutil.rmtree(data_dir, ignore_errors=True)
            raise LifecycleError(
                f"platform '{req.platform_id}' is not yet implemented; "
                f"choose paper or bedrock"
            ) from e
        except httpx.HTTPStatusError as e:
            shutil.rmtree(data_dir, ignore_errors=True)
            if e.response.status_code == 404:
                raise LifecycleError(
                    f"version '{version}' not found for {req.platform_id}"
                ) from e
            raise LifecycleError(
                f"{req.platform_id} upstream returned HTTP {e.response.status_code}: {e}"
            ) from e
        except (ValueError, RuntimeError) as e:
            # ValueError: Paper "no builds for X"; RuntimeError: Bedrock schema drift / zip slip
            shutil.rmtree(data_dir, ignore_errors=True)
            raise LifecycleError(f"install failed: {e}") from e

        # Accept EULA on the user's behalf. Using ndrchst to create a Minecraft
        # server is treated as agreement (per project policy); we cannot present
        # an interactive EULA prompt in a UI workflow, and refusing to write
        # the file just means the first start fails.
        accept_eula(platform.family, data_dir)

        # Cross-play is Java-only. Bedrock servers ARE bedrock.
        # _validate already enforced family + non-collision; just install + wire.
        # Geyser binds to the *internal* container port (19132); Docker maps
        # host:bedrock_bridge_port → container:19132. If we passed the host
        # port to install_cross_play, Geyser would bind 19150 inside while
        # Docker forwards to 19132 — traffic vanishes.
        if req.cross_play:
            await install_cross_play(data_dir, java_port=25565)

        server = Server(
            id=server_id,
            name=req.name,
            platform_id=req.platform_id,
            family=platform.family,
            version=version,
            port=req.port,
            memory_mb=req.memory_mb,
            cross_play=req.cross_play,
            bedrock_bridge_port=req.bedrock_bridge_port if req.cross_play else None,
        )

        spec = _build_spec(server, data_dir)
        container_id = await self._docker.create_container(spec)
        server.container_id = container_id

        srv_store.insert(self._conn, server)

        # Generate the per-server pilot bundle for Java servers. Cheap (no
        # compile step), version-locked, so the public surface can serve a
        # client guaranteed to match this exact server.
        if server.family is Family.JAVA:
            try:
                build_pilot_bundle(server, public_host=self._public_host)
            except PilotBuildError:
                # Pilot is best-effort — log but don't fail server create
                import logging
                logging.getLogger("ndrchst").warning(
                    "pilot bundle build failed for %s; server created anyway", server.id,
                    exc_info=True,
                )

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
                shutil.rmtree(data_dir, ignore_errors=True)
        # Always drop the pilot bundle — once the server is gone, the bundle
        # points at a dead address and serves no one.
        remove_pilot_bundle(server_id)
        srv_store.delete(self._conn, server_id)

    async def stats(self, server_id: str):
        """Return Docker container stats for the server, or None if no
        container exists (e.g. server was never started)."""
        server = self._must_get(server_id)
        if server.container_id is None:
            return None
        return await self._docker.stats(server.container_id)

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
