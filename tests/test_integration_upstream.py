"""Live contract tests against real upstream APIs.

Run with:  pytest -m integration

These guard against schema drift. They cost real network traffic and skip by
default.
"""
from __future__ import annotations

import pytest

from ndrchst.mods.base import AssetKind
from ndrchst.mods.modrinth import Modrinth
from ndrchst.platforms.bedrock import Bedrock
from ndrchst.platforms.paper import Paper

pytestmark = pytest.mark.integration


async def test_paper_versions_live():
    p = Paper()
    versions = await p.versions()
    assert len(versions) > 0
    # Newest first; current MC line is 1.x
    assert versions[0].version.startswith(("1.21", "1.22", "1.23"))


async def test_paper_latest_build_live():
    p = Paper()
    versions = await p.versions()
    build = await p.latest_build(versions[0].version)
    assert isinstance(build, int) and build > 0


async def test_bedrock_resolves_linux_download_live():
    b = Bedrock()
    versions = await b.versions()
    assert len(versions) == 1
    # Sanity: looks like a real BDS version (W.X.Y.Z, four parts)
    parts = versions[0].version.split(".")
    assert len(parts) == 4
    assert all(p.isdigit() for p in parts)


async def test_modrinth_search_live():
    m = Modrinth()
    hits = await m.search("fabric api", kind=AssetKind.MOD, loader="fabric", game_version="1.21")
    assert len(hits) > 0
    assert any("fabric" in h.name.lower() for h in hits)


async def test_modrinth_versions_live():
    m = Modrinth()
    # Fabric API has the well-known slug "fabric-api"
    versions = await m.versions("fabric-api", loader="fabric", game_version="1.21")
    assert len(versions) > 0
    v = versions[0]
    assert v.download_url.startswith("https://")
    assert v.sha1  # Modrinth always publishes sha1
    assert v.file_size > 0
