"""Mod installer tests."""
from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path

import httpx
import pytest

from ndrchst.domain.models import Family, Server
from ndrchst.mods.base import AssetKind
from ndrchst.mods.modrinth import Version
from ndrchst.runtime.installer import (
    InstallerError,
    install,
    list_installed,
    record_install,
    remove_installed,
)
from ndrchst.store import servers as srv_store
from ndrchst.store.db import connect


def _version(content: bytes, *, name: str = "thing-1.0.jar") -> Version:
    return Version(
        project_id="abc123",
        version_number="1.0",
        file_name=name,
        download_url="https://cdn/thing.jar",
        file_size=len(content),
        sha1=hashlib.sha1(content).hexdigest(),
        game_versions=("1.21",),
        loaders=("fabric",),
        dependencies=(),
        release_type="release",
        published_at="2025-01-01T00:00:00Z",
    )


def _handler(bytes_: bytes):
    def h(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://cdn/thing.jar":
            return httpx.Response(200, content=bytes_)
        return httpx.Response(404)
    return h


async def test_install_java_mod(tmp_path: Path):
    content = b"PK\x03\x04fake-mod"
    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler(content)))
    result = await install(
        data_dir=tmp_path, family=Family.JAVA, kind=AssetKind.MOD,
        version=_version(content), client=client,
    )
    assert (tmp_path / "mods" / "thing-1.0.jar").read_bytes() == content
    assert result.file_path == tmp_path / "mods" / "thing-1.0.jar"
    await client.aclose()


async def test_install_java_plugin_goes_to_plugins_dir(tmp_path: Path):
    content = b"PK\x03\x04plugin"
    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler(content)))
    await install(
        data_dir=tmp_path, family=Family.JAVA, kind=AssetKind.PLUGIN,
        version=_version(content), client=client,
    )
    assert (tmp_path / "plugins" / "thing-1.0.jar").exists()
    await client.aclose()


async def test_install_datapack_goes_to_world_datapacks(tmp_path: Path):
    content = b"PK\x03\x04dp"
    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler(content)))
    await install(
        data_dir=tmp_path, family=Family.JAVA, kind=AssetKind.DATAPACK,
        version=_version(content), client=client,
    )
    assert (tmp_path / "world" / "datapacks" / "thing-1.0.jar").exists()
    await client.aclose()


async def test_install_bedrock_resource_pack(tmp_path: Path):
    content = b"PK\x03\x04bp"
    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler(content)))
    await install(
        data_dir=tmp_path, family=Family.BEDROCK, kind=AssetKind.RESOURCEPACK,
        version=_version(content, name="pack.mcpack"), client=client,
    )
    assert (tmp_path / "resource_packs" / "pack.mcpack").exists()
    await client.aclose()


async def test_install_rejects_mod_on_bedrock(tmp_path: Path):
    content = b"x"
    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler(content)))
    with pytest.raises(InstallerError, match="not installable"):
        await install(
            data_dir=tmp_path, family=Family.BEDROCK, kind=AssetKind.MOD,
            version=_version(content), client=client,
        )
    await client.aclose()


async def test_install_sha1_mismatch_cleans_up(tmp_path: Path):
    content = b"actual"
    # Build a Version whose declared sha1 belongs to OTHER content
    bad = dataclasses.replace(
        _version(b"different-content-than-server-returns"),
        download_url="https://cdn/thing.jar",
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler(content)))
    with pytest.raises(InstallerError, match="sha1 mismatch"):
        await install(
            data_dir=tmp_path, family=Family.JAVA, kind=AssetKind.MOD,
            version=bad, client=client,
        )
    assert not (tmp_path / "mods" / "thing-1.0.jar").exists()
    await client.aclose()


def test_record_install_round_trip(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    # Need a server row because installed_assets has a FK
    srv_store.insert(conn, Server(
        id="abc", name="s", platform_id="paper", family=Family.JAVA,
        version="1.21.3", port=25565, memory_mb=2048,
    ))
    record_install(
        conn,
        server_id="abc",
        source_id="modrinth",
        kind=AssetKind.MOD,
        result=type("R", (), {"asset_id": "fabric-api", "version": "0.100.0"}),
    )
    installed = list_installed(conn, "abc")
    assert len(installed) == 1
    assert installed[0]["asset_id"] == "fabric-api"
    assert installed[0]["kind"] == "mod"

    remove_installed(conn, "abc", "modrinth", "fabric-api")
    assert list_installed(conn, "abc") == []


def test_installed_assets_cascades_on_server_delete(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    srv_store.insert(conn, Server(
        id="abc", name="s", platform_id="paper", family=Family.JAVA,
        version="1.21.3", port=25565, memory_mb=2048,
    ))
    record_install(
        conn,
        server_id="abc", source_id="modrinth", kind=AssetKind.MOD,
        result=type("R", (), {"asset_id": "x", "version": "1"}),
    )
    srv_store.delete(conn, "abc")
    assert list_installed(conn, "abc") == []
