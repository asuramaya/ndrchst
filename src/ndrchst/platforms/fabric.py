from __future__ import annotations

from pathlib import Path

from .base import Family, InstallArtifact, Platform, VersionInfo

FABRIC_META = "https://meta.fabricmc.net/v2/versions"


class Fabric(Platform):
    id = "fabric"
    family = Family.JAVA
    display_name = "Fabric"

    async def versions(self) -> list[VersionInfo]:
        raise NotImplementedError

    async def install(self, version: str, dest: Path) -> InstallArtifact:
        raise NotImplementedError
