"""Core dataclasses shared across the project."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..platforms.base import Family


class ServerStatus(str, Enum):
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
    created_at: datetime = field(default_factory=datetime.utcnow)
