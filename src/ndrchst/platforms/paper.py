"""PaperMC platform.

API: https://api.papermc.io/v2/projects/paper
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import httpx

from .base import Family, InstallArtifact, Platform, VersionInfo

PAPER_API = "https://api.papermc.io/v2/projects/paper"


class Paper(Platform):
    id = "paper"
    family = Family.JAVA
    display_name = "Paper"

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def versions(self) -> list[VersionInfo]:
        client = await self._http()
        r = await client.get(PAPER_API)
        r.raise_for_status()
        data = r.json()
        # newest last in Paper's response; reverse for newest-first
        return [VersionInfo(version=v) for v in reversed(data["versions"])]

    async def latest_build(self, version: str) -> int:
        client = await self._http()
        r = await client.get(f"{PAPER_API}/versions/{version}/builds")
        r.raise_for_status()
        builds = r.json().get("builds") or []
        if not builds:
            raise ValueError(f"no builds for paper {version}")
        return int(builds[-1]["build"])

    async def install(self, version: str, dest: Path) -> InstallArtifact:
        dest.mkdir(parents=True, exist_ok=True)
        client = await self._http()

        build = await self.latest_build(version)
        meta = (await client.get(f"{PAPER_API}/versions/{version}/builds/{build}")).json()
        download = meta["downloads"]["application"]
        filename = download["name"]
        expected_sha = download["sha256"]

        url = (
            f"{PAPER_API}/versions/{version}/builds/{build}/downloads/{filename}"
        )
        jar_path = dest / "server.jar"
        sha = hashlib.sha256()
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with jar_path.open("wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                    f.write(chunk)
                    sha.update(chunk)

        if sha.hexdigest() != expected_sha:
            jar_path.unlink(missing_ok=True)
            raise ValueError(
                f"paper {version} build {build} sha256 mismatch "
                f"(expected {expected_sha}, got {sha.hexdigest()})"
            )

        return InstallArtifact(path=dest, entrypoint="server.jar")
