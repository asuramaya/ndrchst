"""Modrinth source (api.modrinth.com v2).

Differences from the v2 port:
  * async-native via httpx
  * facets built with a typed helper instead of stringly-typed lists-of-lists
  * version filtering pushed to the server via `game_versions` + `loaders`
    query params (the v2 code did this for versions but not consistently)
  * download SHA1 surfaced from the file metadata for caller verification
  * no global cache (the cache landed in v2 around v1.5 and accumulated bugs
    — re-add only when load measurements justify it)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from .base import Asset, AssetKind, Source

MODRINTH_API = "https://api.modrinth.com/v2"

# Modrinth's project_type taxonomy
_KIND_TO_PROJECT_TYPE: dict[AssetKind, str] = {
    AssetKind.MOD: "mod",
    AssetKind.PLUGIN: "mod",  # Modrinth treats plugins as mods + bukkit/paper loader
    AssetKind.DATAPACK: "datapack",
    AssetKind.RESOURCEPACK: "resourcepack",
    AssetKind.WORLD: "modpack",  # closest match; bedrock behavior packs use different feed
    AssetKind.BEHAVIORPACK: "resourcepack",  # Modrinth lumps bedrock packs under resourcepack
}


@dataclass(frozen=True, slots=True)
class Version:
    """A specific downloadable build of a Modrinth project."""
    project_id: str
    version_number: str
    file_name: str
    download_url: str
    file_size: int
    sha1: str
    game_versions: tuple[str, ...]
    loaders: tuple[str, ...]
    dependencies: tuple[dict, ...]
    release_type: str
    published_at: str


def _build_facets(
    *,
    kind: AssetKind | None,
    loader: str | None,
    game_version: str | None,
) -> list[list[str]]:
    facets: list[list[str]] = []
    if kind is not None:
        facets.append([f"project_type:{_KIND_TO_PROJECT_TYPE[kind]}"])
    if loader is not None:
        facets.append([f"categories:{loader}"])
    if game_version is not None:
        facets.append([f"versions:{game_version}"])
    return facets


class Modrinth(Source):
    id = "modrinth"
    display_name = "Modrinth"

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={"User-Agent": "ndrchst/0.0.1 (+github.com/asuramaya/ndrchst)"},
            )
        return self._client

    async def search(
        self,
        query: str,
        kind: AssetKind | None = None,
        *,
        loader: str | None = None,
        game_version: str | None = None,
        limit: int = 20,
    ) -> list[Asset]:
        client = await self._http()
        params: list[tuple[str, str]] = [("query", query), ("limit", str(limit))]
        facets = _build_facets(kind=kind, loader=loader, game_version=game_version)
        if facets:
            params.append(("facets", json.dumps(facets)))

        r = await client.get(f"{MODRINTH_API}/search", params=params)
        r.raise_for_status()
        hits = r.json().get("hits", [])
        return [
            Asset(
                source_id=self.id,
                id=hit.get("project_id") or hit["slug"],
                name=hit.get("title", ""),
                kind=kind or AssetKind.MOD,
                summary=hit.get("description", ""),
                download_url="",  # populated via versions()
                mc_version=game_version,
                loader=loader,
            )
            for hit in hits
        ]

    async def latest_by_hash(
        self,
        hashes: list[str],
        *,
        loaders: list[str] | None = None,
        game_versions: list[str] | None = None,
    ) -> dict[str, Version]:
        """Map each input SHA1 → its project's latest matching version.

        Uses Modrinth's batch endpoint ``POST /v2/version_files/update`` which
        accepts a list of file hashes plus loader/game-version filters and
        returns one ``Version`` object per known file (unknown hashes are
        silently absent from the response).

        Returns a dict keyed by the *input* hash so callers can correlate
        back to the file they uploaded.
        """
        if not hashes:
            return {}
        client = await self._http()
        body: dict = {"hashes": hashes, "algorithm": "sha1"}
        if loaders:
            body["loaders"] = loaders
        if game_versions:
            body["game_versions"] = game_versions
        r = await client.post(
            f"{MODRINTH_API}/version_files/update", json=body,
        )
        r.raise_for_status()
        payload = r.json()
        out: dict[str, Version] = {}
        for input_hash, v in payload.items():
            files = v.get("files") or []
            primary = next((f for f in files if f.get("primary")), files[0] if files else {})
            if not primary:
                continue
            file_hashes = primary.get("hashes") or {}
            out[input_hash] = Version(
                project_id=v.get("project_id", ""),
                version_number=v.get("version_number", ""),
                file_name=primary.get("filename", ""),
                download_url=primary.get("url", ""),
                file_size=int(primary.get("size") or 0),
                sha1=file_hashes.get("sha1", ""),
                game_versions=tuple(v.get("game_versions") or ()),
                loaders=tuple(v.get("loaders") or ()),
                dependencies=tuple(v.get("dependencies") or ()),
                release_type=v.get("version_type", "release"),
                published_at=v.get("date_published", ""),
            )
        return out

    async def versions(
        self,
        asset_id: str,
        *,
        loader: str | None = None,
        game_version: str | None = None,
    ) -> list[Version]:
        client = await self._http()
        params: list[tuple[str, str]] = []
        if game_version is not None:
            params.append(("game_versions", quote(json.dumps([game_version]))))
        if loader is not None:
            params.append(("loaders", quote(json.dumps([loader]))))

        url = f"{MODRINTH_API}/project/{asset_id}/version"
        if params:
            url += "?" + "&".join(f"{k}={v}" for k, v in params)

        r = await client.get(url)
        r.raise_for_status()
        out: list[Version] = []
        for v in r.json():
            files = v.get("files") or []
            primary = next((f for f in files if f.get("primary")), files[0] if files else {})
            if not primary:
                continue
            hashes = primary.get("hashes") or {}
            out.append(
                Version(
                    project_id=asset_id,
                    version_number=v.get("version_number", ""),
                    file_name=primary.get("filename", ""),
                    download_url=primary.get("url", ""),
                    file_size=int(primary.get("size") or 0),
                    sha1=hashes.get("sha1", ""),
                    game_versions=tuple(v.get("game_versions") or ()),
                    loaders=tuple(v.get("loaders") or ()),
                    dependencies=tuple(v.get("dependencies") or ()),
                    release_type=v.get("version_type", "release"),
                    published_at=v.get("date_published", ""),
                )
            )
        return out
