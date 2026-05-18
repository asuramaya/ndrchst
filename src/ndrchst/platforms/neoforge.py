from __future__ import annotations

from pathlib import Path

from .base import Family, InstallArtifact, Platform, VersionInfo

NEOFORGE_MAVEN = "https://maven.neoforged.net/api/maven/versions/releases/net/neoforged/neoforge"


class NeoForge(Platform):
    id = "neoforge"
    family = Family.JAVA
    display_name = "NeoForge"

    async def versions(self) -> list[VersionInfo]:
        raise NotImplementedError

    async def install(self, version: str, dest: Path) -> InstallArtifact:
        raise NotImplementedError
