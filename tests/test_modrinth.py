"""Modrinth source tests using httpx MockTransport.

Stresses:
  - search builds correct facets when filters are present
  - search omits facets entirely when no filters
  - versions endpoint surfaces sha1 + picks the primary file
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import httpx

from ndrchst.mods.base import AssetKind
from ndrchst.mods.modrinth import MODRINTH_API, Modrinth


def _capture():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        path = request.url.path
        if path == "/v2/search":
            return httpx.Response(
                200,
                json={
                    "hits": [
                        {
                            "project_id": "abc123",
                            "slug": "fabric-api",
                            "title": "Fabric API",
                            "description": "Essential API",
                            "downloads": 999,
                        }
                    ]
                },
            )
        if path.startswith("/v2/project/abc123/version"):
            return httpx.Response(
                200,
                json=[
                    {
                        "version_number": "0.100.0+1.21",
                        "version_type": "release",
                        "date_published": "2025-01-01T00:00:00Z",
                        "game_versions": ["1.21"],
                        "loaders": ["fabric"],
                        "dependencies": [],
                        "files": [
                            # non-primary first to test selection logic
                            {"primary": False, "filename": "sources.jar", "url": "x", "size": 0},
                            {
                                "primary": True,
                                "filename": "fabric-api-0.100.0.jar",
                                "url": "https://cdn/fabric.jar",
                                "size": 12345,
                                "hashes": {"sha1": "deadbeef"},
                            },
                        ],
                    }
                ],
            )
        return httpx.Response(404)

    return captured, handler


async def test_search_builds_facets():
    captured, h = _capture()
    client = httpx.AsyncClient(transport=httpx.MockTransport(h))
    m = Modrinth(client=client)

    await m.search("api", kind=AssetKind.MOD, loader="fabric", game_version="1.21")

    req = captured[0]
    qs = parse_qs(urlsplit(str(req.url)).query)
    facets = json.loads(qs["facets"][0])
    assert ["project_type:mod"] in facets
    assert ["categories:fabric"] in facets
    assert ["versions:1.21"] in facets
    await client.aclose()


async def test_search_omits_facets_when_no_filters():
    captured, h = _capture()
    client = httpx.AsyncClient(transport=httpx.MockTransport(h))
    m = Modrinth(client=client)
    await m.search("optifine")
    qs = parse_qs(urlsplit(str(captured[0].url)).query)
    assert "facets" not in qs
    await client.aclose()


async def test_versions_picks_primary_and_surfaces_sha1():
    _, h = _capture()
    client = httpx.AsyncClient(transport=httpx.MockTransport(h))
    m = Modrinth(client=client)
    versions = await m.versions("abc123", loader="fabric", game_version="1.21")
    assert len(versions) == 1
    v = versions[0]
    assert v.file_name == "fabric-api-0.100.0.jar"
    assert v.download_url == "https://cdn/fabric.jar"
    assert v.sha1 == "deadbeef"
    assert v.file_size == 12345
    await client.aclose()
