"""Bedrock Dedicated Server (BDS) — first-class platform.

Bedrock is NOT a Java derivative. It uses Mojang's native dedicated server
binary distributed as a zip from minecraft.net. Two modes are supported:

  1. Native BDS  — runs `bedrock_server` inside a Docker container with the
     official Mojang binary. Connects from Bedrock clients (Win10, mobile,
     consoles) on UDP/19132.

  2. Geyser-on-Java — see runtime.geyser; not a Platform, it's a plugin layer
     that lets a Java server (Paper/Purpur/Spigot) accept Bedrock clients.

This module covers (1). Geyser/Floodgate auto-install for Java servers lives
in runtime/geyser.py and is wired from the Java platforms when the user opts
into cross-play.

Notes:
  * Bedrock has no mod loader equivalent — content is delivered via
    behavior packs / resource packs (mods/ folder treats these as a
    distinct artifact type).
  * EULA acceptance is required at install time, identical to Java.
  * Mojang rotates the download URL; we resolve it by scraping the
    versions feed (no official API exists at time of writing).
"""
from __future__ import annotations

from pathlib import Path

from .base import Family, InstallArtifact, Platform, VersionInfo

# Mojang publishes BDS via a JSON feed that the launcher uses.
# This URL is unofficial but stable; we'll validate at install time.
BDS_VERSIONS_FEED = "https://net-secondary.web.minecraft-services.net/api/v1.0/download/links"


class Bedrock(Platform):
    id = "bedrock"
    family = Family.BEDROCK
    display_name = "Bedrock"

    async def versions(self) -> list[VersionInfo]:
        raise NotImplementedError

    async def install(self, version: str, dest: Path) -> InstallArtifact:
        raise NotImplementedError
