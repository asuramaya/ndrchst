from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable


class Family(StrEnum):
    JAVA = "java"
    BEDROCK = "bedrock"


@dataclass(frozen=True, slots=True)
class VersionInfo:
    version: str
    stable: bool = True
    build: str | None = None  # for platforms with sub-builds (paper, purpur)


@dataclass(frozen=True, slots=True)
class InstallArtifact:
    """Result of resolving a version into something runnable."""
    path: Path
    entrypoint: str  # e.g. "server.jar" or "bedrock_server"
    extra_files: tuple[Path, ...] = ()


@runtime_checkable
class Platform(Protocol):
    id: str
    family: Family
    display_name: str

    async def versions(self) -> list[VersionInfo]: ...

    async def install(self, version: str, dest: Path) -> InstallArtifact: ...
