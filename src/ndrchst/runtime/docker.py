"""Docker runtime — the only supported runtime in v0.

Wraps the (synchronous) docker-py SDK with asyncio.to_thread. We deliberately
do NOT use itzg/minecraft-server: our platforms/* modules own the install path
(version pinning, SHA verification, zip-slip guards), and we want the runtime
layer to be a thin shell that just *runs* what's already on disk.

Per-server layout on the host:
    ~/.ndrchst/servers/<server_id>/
      Java:    server.jar, world/, plugins/, mods/, server.properties, ...
      Bedrock: bedrock_server, worlds/, server.properties, valid_known_packs.json

Java image:    eclipse-temurin:21-jre   (covers MC 1.20.5+; 1.17-1.20.4 uses :17-jre)
Bedrock image: ubuntu:24.04             (BDS is a native ELF; only needs glibc)
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import docker
from docker.errors import ImageNotFound, NotFound

from ..domain.models import Family, ServerStatus

# Java major-version cutover. 1.20.5+ wants Java 21.
_JAVA21_MIN_MC = (1, 20, 5)

JAVA_IMAGE_21 = "eclipse-temurin:21-jre"
JAVA_IMAGE_17 = "eclipse-temurin:17-jre"
BEDROCK_IMAGE = "ubuntu:24.04"

_CONTAINER_DATA_DIR = "/data"
_LABEL = "io.ndrchst.managed"


def java_image_for(mc_version: str) -> str:
    """Pick the right JRE image for a Minecraft version. Defaults to 21
    if the version is unparseable (modern is the safer fallback)."""
    try:
        parts = tuple(int(p) for p in mc_version.split(".")[:3])
        # pad to 3 parts
        parts = parts + (0,) * (3 - len(parts))
        return JAVA_IMAGE_21 if parts >= _JAVA21_MIN_MC else JAVA_IMAGE_17
    except ValueError:
        return JAVA_IMAGE_21


@dataclass(frozen=True, slots=True)
class ContainerSpec:
    """Everything `create_container` needs. Built by lifecycle.py from a Server.

    ``ports`` keys carry the protocol explicitly (e.g. ``"25565/tcp"``,
    ``"19132/udp"``) — needed because cross-play Java servers expose both
    TCP (Paper) and UDP (Geyser) in the same container.

    Port values are either:
      * int  — bind to 0.0.0.0:<port> (public exposure)
      * tuple(host_ip, host_port) — bind to <host_ip>:<host_port>
    """
    name: str           # docker container name, e.g. "ndrchst-srv-abc"
    image: str
    cmd: list[str]
    workdir: str
    data_dir: Path      # host path mounted at /data
    ports: dict[str, int | tuple[str, int]]
    memory_mb: int
    server_id: str      # tagged into label
    family: Family
    env: dict[str, str]


@dataclass(frozen=True, slots=True)
class ContainerStats:
    cpu_percent: float
    memory_used_mb: int
    memory_limit_mb: int


class _DockerClient(Protocol):
    """Just the surface of docker.DockerClient we actually touch.

    Lets tests inject a fake without monkey-patching the import.
    """
    def ping(self) -> bool: ...
    @property
    def containers(self): ...  # docker.client.ContainerCollection-shaped


class Docker:
    """Thin async wrapper around docker-py."""

    def __init__(self, client: _DockerClient | None = None):
        self._client = client or docker.from_env()

    async def ping(self) -> bool:
        return await asyncio.to_thread(self._client.ping)

    async def create_container(self, spec: ContainerSpec) -> str:
        """Idempotent: removes any existing container with the same name first.

        Pulls the image if it is not present locally. docker-py's
        ``containers.create()`` does not pull on miss — without this, the very
        first container for any image fails with ImageNotFound.
        """
        await self._remove_if_exists(spec.name)
        await self._ensure_image(spec.image)

        def _create() -> str:
            c = self._client.containers.create(
                image=spec.image,
                command=spec.cmd,
                name=spec.name,
                working_dir=spec.workdir,
                detach=True,
                stdin_open=True,    # for Bedrock stdin commands
                tty=False,
                volumes={
                    str(spec.data_dir.resolve()): {
                        "bind": _CONTAINER_DATA_DIR,
                        "mode": "rw",
                    }
                },
                ports=dict(spec.ports),
                mem_limit=f"{spec.memory_mb}m",
                memswap_limit=f"{spec.memory_mb}m",  # disable swap
                restart_policy={"Name": "unless-stopped"},
                labels={_LABEL: spec.server_id, "io.ndrchst.family": spec.family.value},
                environment=spec.env,
            )
            return c.id

        return await asyncio.to_thread(_create)

    async def _ensure_image(self, image: str) -> None:
        def _check_and_pull() -> None:
            try:
                self._client.images.get(image)
            except ImageNotFound:
                self._client.images.pull(image)
        await asyncio.to_thread(_check_and_pull)

    async def start(self, container_id: str) -> None:
        await asyncio.to_thread(self._container(container_id).start)

    async def stop(self, container_id: str, timeout: int = 30) -> None:
        await asyncio.to_thread(self._container(container_id).stop, timeout=timeout)

    async def restart(self, container_id: str, timeout: int = 30) -> None:
        await asyncio.to_thread(self._container(container_id).restart, timeout=timeout)

    async def remove(self, container_id: str, *, force: bool = False) -> None:
        try:
            c = self._container(container_id)
            await asyncio.to_thread(c.remove, force=force)
        except NotFound:
            return

    async def status(self, container_id: str) -> ServerStatus:
        try:
            c = await asyncio.to_thread(self._client.containers.get, container_id)
        except NotFound:
            return ServerStatus.STOPPED
        return _docker_state_to_status(c.status, c.attrs.get("State", {}))

    async def logs(self, container_id: str, *, lines: int = 100) -> str:
        def _get() -> bytes:
            return self._container(container_id).logs(tail=lines)
        raw = await asyncio.to_thread(_get)
        return raw.decode("utf-8", errors="replace")

    async def stats(self, container_id: str) -> ContainerStats:
        def _get() -> dict:
            return self._container(container_id).stats(stream=False)
        raw = await asyncio.to_thread(_get)
        return _parse_stats(raw)

    async def send_stdin(self, container_id: str, payload: str) -> None:
        """Write to the container's stdin. Used by Bedrock; Java should
        prefer RCON for command dispatch."""
        def _send() -> None:
            c = self._container(container_id)
            sock = c.attach_socket(params={"stdin": 1, "stream": 1})
            try:
                # docker-py returns either a SocketIO wrapper or the raw socket
                raw = getattr(sock, "_sock", sock)
                raw.send((payload.rstrip("\n") + "\n").encode("utf-8"))
            finally:
                sock.close()
        await asyncio.to_thread(_send)

    async def list_managed(self) -> list[dict]:
        def _list() -> list[dict]:
            cs = self._client.containers.list(all=True, filters={"label": _LABEL})
            return [
                {
                    "id": c.id,
                    "name": c.name,
                    "server_id": c.labels.get(_LABEL),
                    "family": c.labels.get("io.ndrchst.family"),
                    "status": c.status,
                }
                for c in cs
            ]
        return await asyncio.to_thread(_list)

    # ─── internals ────────────────────────────────────────────────────────

    def _container(self, container_id: str):
        return self._client.containers.get(container_id)

    async def _remove_if_exists(self, name: str) -> None:
        def _do() -> None:
            try:
                c = self._client.containers.get(name)
                c.remove(force=True)
            except NotFound:
                pass
        await asyncio.to_thread(_do)


def _docker_state_to_status(short: str, full: dict) -> ServerStatus:
    # docker container states: created, running, paused, restarting, removing,
    # exited, dead. We project to our ServerStatus.
    if short == "running":
        return ServerStatus.RUNNING
    if short == "restarting":
        return ServerStatus.STARTING
    if short == "exited":
        # exit_code != 0 → crashed
        if int(full.get("ExitCode", 0)) != 0:
            return ServerStatus.CRASHED
        return ServerStatus.STOPPED
    if short == "dead":
        return ServerStatus.CRASHED
    if short == "created":
        return ServerStatus.CREATED
    return ServerStatus.STOPPED


def _parse_stats(raw: dict) -> ContainerStats:
    """Compute CPU% the same way `docker stats` does."""
    cpu = raw.get("cpu_stats", {})
    pre = raw.get("precpu_stats", {})
    cpu_delta = cpu.get("cpu_usage", {}).get("total_usage", 0) - pre.get(
        "cpu_usage", {}
    ).get("total_usage", 0)
    system_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
    online_cpus = cpu.get("online_cpus") or len(
        cpu.get("cpu_usage", {}).get("percpu_usage") or []
    ) or 1
    cpu_pct = (cpu_delta / system_delta) * online_cpus * 100.0 if system_delta > 0 else 0.0

    mem = raw.get("memory_stats", {})
    mem_used = int(mem.get("usage", 0)) - int(mem.get("stats", {}).get("cache", 0))
    mem_limit = int(mem.get("limit", 0))

    return ContainerStats(
        cpu_percent=round(max(cpu_pct, 0.0), 2),
        memory_used_mb=max(mem_used, 0) // (1024 * 1024),
        memory_limit_mb=mem_limit // (1024 * 1024),
    )
