"""Bedrock platform unit tests.

Stresses:
  - parse version from Mojang's URL filename
  - feed schema drift: linux entry missing → RuntimeError
  - install extracts the zip and chmods bedrock_server +x
  - zip slip guard rejects path-traversal entries
"""
from __future__ import annotations

import io
import stat
import zipfile
from pathlib import Path

import httpx
import pytest

from ndrchst.platforms.bedrock import (
    BDS_LINKS_FEED,
    Bedrock,
    _parse_version_from_url,
)


def _make_zip(*, slip: bool = False) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("bedrock_server", b"\x7fELF...native binary...")
        zf.writestr("server.properties", b"server-name=Default\n")
        zf.writestr("permissions.json", b"[]\n")
        if slip:
            zf.writestr("../escape.txt", b"oops")
    return buf.getvalue()


def _feed(url: str | None) -> dict:
    links = []
    if url:
        links.append({"downloadType": "serverBedrockLinux", "downloadUrl": url})
    # also include other types to mimic the real shape
    links.append({"downloadType": "serverBedrockWindows", "downloadUrl": "https://example/win.zip"})
    return {"result": {"links": links}}


def _handler(url: str | None, zip_bytes: bytes):
    def h(request: httpx.Request) -> httpx.Response:
        if str(request.url) == BDS_LINKS_FEED:
            return httpx.Response(200, json=_feed(url))
        if url and str(request.url) == url:
            return httpx.Response(200, content=zip_bytes)
        return httpx.Response(404)
    return h


def test_parse_version_from_url():
    url = "https://www.minecraft.net/bedrockdedicatedserver/bin-linux/bedrock-server-1.21.51.02.zip"
    assert _parse_version_from_url(url) == "1.21.51.02"


def test_parse_version_rejects_unknown_format():
    with pytest.raises(ValueError):
        _parse_version_from_url("https://example/server.tar.gz")


async def test_versions_returns_single_resolved_build():
    url = "https://example/bedrock-server-1.21.51.02.zip"
    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler(url, b"")))
    b = Bedrock(client=client)
    versions = await b.versions()
    assert len(versions) == 1
    assert versions[0].version == "1.21.51.02"
    await client.aclose()


async def test_install_latest_extracts_and_chmods(tmp_path: Path):
    url = "https://example/bedrock-server-1.21.51.02.zip"
    z = _make_zip()
    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler(url, z)))

    b = Bedrock(client=client)
    artifact = await b.install("latest", tmp_path)

    assert artifact.entrypoint == "bedrock_server"
    bin_path = tmp_path / "bedrock_server"
    assert bin_path.exists()
    assert bin_path.read_bytes().startswith(b"\x7fELF")
    # +x is set on the entrypoint
    assert bin_path.stat().st_mode & stat.S_IXUSR
    await client.aclose()


async def test_install_rejects_version_mismatch(tmp_path: Path):
    url = "https://example/bedrock-server-1.21.51.02.zip"
    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler(url, _make_zip())))
    b = Bedrock(client=client)
    with pytest.raises(ValueError, match="only publishes"):
        await b.install("1.20.0.0", tmp_path)
    await client.aclose()


async def test_feed_missing_linux_entry_raises(tmp_path: Path):
    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler(None, b"")))
    b = Bedrock(client=client)
    with pytest.raises(RuntimeError, match="schema may have drifted"):
        await b.versions()
    await client.aclose()


async def test_install_blocks_zip_slip(tmp_path: Path):
    url = "https://example/bedrock-server-1.21.51.02.zip"
    z = _make_zip(slip=True)
    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler(url, z)))
    b = Bedrock(client=client)
    with pytest.raises(RuntimeError, match="outside dest"):
        await b.install("latest", tmp_path)
    # the file outside dest must not have been written
    assert not (tmp_path.parent / "escape.txt").exists()
    await client.aclose()
