from __future__ import annotations

from pathlib import Path

from .base import Family, InstallArtifact, Platform, VersionInfo

PURPUR_API = "https://api.purpurmc.org/v2/purpur"


class Purpur(Platform):
    id = "purpur"
    family = Family.JAVA
    display_name = "Purpur"

    async def versions(self) -> list[VersionInfo]:
        raise NotImplementedError

    async def install(self, version: str, dest: Path) -> InstallArtifact:
        raise NotImplementedError
