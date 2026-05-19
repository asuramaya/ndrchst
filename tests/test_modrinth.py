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
from ndrchst.mods.modrinth import Modrinth


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


async def test_latest_by_hash_round_trips_modrinth_response():
    """POST /v2/version_files/update returns a map keyed by input hash."""
    body_seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/version_files/update" and request.method == "POST":
            body_seen.append(json.loads(request.content.decode()))
            return httpx.Response(200, json={
                "abc123": {
                    "project_id": "geyser",
                    "version_number": "2.10.1-SNAPSHOT",
                    "version_type": "release",
                    "date_published": "2026-04-01T00:00:00Z",
                    "game_versions": ["1.21", "1.21.11"],
                    "loaders": ["paper", "spigot"],
                    "dependencies": [],
                    "files": [{
                        "primary": True,
                        "filename": "Geyser-Spigot-2.10.1.jar",
                        "url": "https://cdn/geyser-2.10.1.jar",
                        "size": 4321,
                        "hashes": {"sha1": "f00dbabe"},
                    }],
                }
                # `unknown-hash` is intentionally absent so we test silent omission
            })
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    m = Modrinth(client=client)
    out = await m.latest_by_hash(
        ["abc123", "unknown-hash"],
        loaders=["paper", "spigot"],
        game_versions=["1.21.11", "1.21"],
    )
    assert "unknown-hash" not in out
    assert "abc123" in out
    v = out["abc123"]
    assert v.version_number == "2.10.1-SNAPSHOT"
    assert v.download_url == "https://cdn/geyser-2.10.1.jar"
    assert v.sha1 == "f00dbabe"
    # body has both filters + algorithm
    assert body_seen[0]["algorithm"] == "sha1"
    assert body_seen[0]["loaders"] == ["paper", "spigot"]
    assert "1.21" in body_seen[0]["game_versions"]
    await client.aclose()


async def test_latest_by_hash_empty_input_skips_request():
    """Don't even call out if there's nothing to ask about."""
    captured: list[httpx.Request] = []

    def h(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(h))
    m = Modrinth(client=client)
    out = await m.latest_by_hash([])
    assert out == {}
    assert captured == []
    await client.aclose()
