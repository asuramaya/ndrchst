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
    # False for stub platforms whose install() raises NotImplementedError;
    # the UI hides these from the create dropdown so users can't pick them.
    implemented: bool
    # False for platforms we don't want to surface in the UI right now even
    # though the code path is fully implemented (e.g. Bedrock while the
    # product is focused on modded Java). The platform stays registered and
    # importable so the code can be open-sourced or re-enabled later; only
    # the create-form and API listing filter it out.
    default_visible: bool = True

    async def versions(self) -> list[VersionInfo]: ...

    async def install(self, version: str, dest: Path) -> InstallArtifact: ...
