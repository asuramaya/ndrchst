"""Mod / plugin / resource pack / behavior pack sources.

v0 = Modrinth only. Spiget and Hangar are planned plugins.
Bedrock behavior/resource packs are addressed via the same `Source` protocol
but flow into a different install path inside the server data dir.
"""
from .base import Asset, AssetKind, Source
from .modrinth import Modrinth

REGISTRY: dict[str, Source] = {s.id: s for s in (Modrinth(),)}

__all__ = ["Asset", "AssetKind", "Modrinth", "REGISTRY", "Source"]
