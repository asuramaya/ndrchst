"""Server platform installers and version resolvers.

Each platform module exposes a `Platform` implementation. Java platforms
share the Java install/launch model; Bedrock is first-class and uses the
Mojang dedicated server binary.
"""
from __future__ import annotations

from .base import Family, InstallArtifact, Platform, VersionInfo
from .bedrock import Bedrock
from .fabric import Fabric
from .forge import Forge
from .modpack import Modpack
from .neoforge import NeoForge
from .paper import Paper
from .purpur import Purpur
from .vanilla import Vanilla

REGISTRY: dict[str, Platform] = {
    p.id: p
    for p in (Paper(), Purpur(), Vanilla(), Fabric(), Forge(), NeoForge(), Modpack(), Bedrock())
}


def get(platform_id: str) -> Platform:
    try:
        return REGISTRY[platform_id]
    except KeyError as e:
        raise ValueError(f"unknown platform: {platform_id}") from e


__all__ = [
    "REGISTRY",
    "Family",
    "InstallArtifact",
    "Platform",
    "VersionInfo",
    "get",
]
