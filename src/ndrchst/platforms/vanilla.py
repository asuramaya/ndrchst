from __future__ import annotations

from pathlib import Path

from .base import Family, InstallArtifact, Platform, VersionInfo

MOJANG_MANIFEST = "https://launchermeta.mojang.com/mc/game/version_manifest.json"


class Vanilla(Platform):
    id = "vanilla"
    family = Family.JAVA
    display_name = "Vanilla"

    async def versions(self) -> list[VersionInfo]:
        raise NotImplementedError

    async def install(self, version: str, dest: Path) -> InstallArtifact:
        raise NotImplementedError
