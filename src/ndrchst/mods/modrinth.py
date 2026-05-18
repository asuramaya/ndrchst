from __future__ import annotations

from .base import Asset, AssetKind, Source

MODRINTH_API = "https://api.modrinth.com/v2"


class Modrinth(Source):
    id = "modrinth"
    display_name = "Modrinth"

    async def search(self, query: str, kind: AssetKind | None = None) -> list[Asset]:
        raise NotImplementedError

    async def versions(self, asset_id: str) -> list[Asset]:
        raise NotImplementedError
