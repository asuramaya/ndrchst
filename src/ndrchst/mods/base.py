from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable


class AssetKind(str, Enum):
    MOD = "mod"
    PLUGIN = "plugin"
    DATAPACK = "datapack"
    RESOURCEPACK = "resourcepack"
    BEHAVIORPACK = "behaviorpack"   # Bedrock
    WORLD = "world"


@dataclass(frozen=True, slots=True)
class Asset:
    source_id: str
    id: str
    name: str
    kind: AssetKind
    summary: str
    download_url: str
    mc_version: str | None = None
    loader: str | None = None  # fabric / forge / paper / bedrock


@runtime_checkable
class Source(Protocol):
    id: str
    display_name: str

    async def search(self, query: str, kind: AssetKind | None = None) -> list[Asset]: ...

    async def versions(self, asset_id: str) -> list[Asset]: ...
