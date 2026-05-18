from __future__ import annotations

from pathlib import Path

from .base import Family, InstallArtifact, Platform, VersionInfo

PAPER_API = "https://api.papermc.io/v2/projects/paper"


class Paper(Platform):
    id = "paper"
    family = Family.JAVA
    display_name = "Paper"

    async def versions(self) -> list[VersionInfo]:
        raise NotImplementedError

    async def install(self, version: str, dest: Path) -> InstallArtifact:
        raise NotImplementedError
