"""Paper platform unit tests using httpx MockTransport.

Stresses:
  - versions endpoint parsing + newest-first ordering
  - install happy path with sha256 verification
  - install fails (and cleans up) when sha256 mismatches
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from ndrchst.platforms.paper import PAPER_API, Paper


def _make_handler(jar_bytes: bytes, *, lie_about_sha: bool = False):
    actual = hashlib.sha256(jar_bytes).hexdigest()
    reported = "0" * 64 if lie_about_sha else actual

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == PAPER_API:
            return httpx.Response(200, json={"versions": ["1.20.4", "1.21.1", "1.21.3"]})
        if url == f"{PAPER_API}/versions/1.21.3/builds":
            return httpx.Response(200, json={"builds": [{"build": 100}, {"build": 101}]})
        if url == f"{PAPER_API}/versions/1.21.3/builds/101":
            return httpx.Response(
                200,
                json={
                    "downloads": {
                        "application": {
                            "name": "paper-1.21.3-101.jar",
                            "sha256": reported,
                        }
                    }
                },
            )
        if url == f"{PAPER_API}/versions/1.21.3/builds/101/downloads/paper-1.21.3-101.jar":
            return httpx.Response(200, content=jar_bytes)
        return httpx.Response(404)

    return handler


async def test_versions_newest_first():
    client = httpx.AsyncClient(transport=httpx.MockTransport(_make_handler(b"x")))
    p = Paper(client=client)
    versions = await p.versions()
    assert [v.version for v in versions] == ["1.21.3", "1.21.1", "1.20.4"]
    await client.aclose()


async def test_install_writes_jar_and_verifies_sha(tmp_path: Path):
    fake_jar = b"PK\x03\x04" + b"X" * 1024
    client = httpx.AsyncClient(transport=httpx.MockTransport(_make_handler(fake_jar)))
    p = Paper(client=client)

    artifact = await p.install("1.21.3", tmp_path)

    assert artifact.entrypoint == "server.jar"
    assert (tmp_path / "server.jar").read_bytes() == fake_jar
    await client.aclose()


async def test_install_aborts_on_sha_mismatch(tmp_path: Path):
    fake_jar = b"corrupted"
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(_make_handler(fake_jar, lie_about_sha=True))
    )
    p = Paper(client=client)

    with pytest.raises(ValueError, match="sha256 mismatch"):
        await p.install("1.21.3", tmp_path)

    # cleanup: jar should be removed when verification fails
    assert not (tmp_path / "server.jar").exists()
    await client.aclose()
