"""Core dataclasses shared across the project."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from ..platforms.base import Family


def _now() -> datetime:
    return datetime.now(UTC)


class ServerStatus(StrEnum):
    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    CRASHED = "crashed"


@dataclass(slots=True)
class Server:
    id: str
    name: str
    platform_id: str
    family: Family
    version: str
    port: int
    memory_mb: int
    status: ServerStatus = ServerStatus.CREATED
    container_id: str | None = None
    cross_play: bool = False  # Java + Geyser
    # Host UDP port that Geyser exposes for Bedrock clients (cross-play only).
    bedrock_bridge_port: int | None = None
    # Host TCP port mapping for the container's RCON listener (Java only).
    # Set at create-time; lets the console UI dispatch commands without going
    # through the public MC port.
    rcon_port: int | None = None
    rcon_password: str | None = None
    # User-supplied JVM args appended to the launch command (Java only).
    # Example: "-XX:+UseG1GC -Daikar.flags=true". Empty/None → no extras.
    extra_jvm_flags: str | None = None
    # User-supplied container env vars as "KEY=VALUE" lines. Merged on top of
    # the runtime-required env (EULA, LD_LIBRARY_PATH).
    env_vars: str | None = None
    created_at: datetime = field(default_factory=_now)
