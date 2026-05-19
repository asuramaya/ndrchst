"""Bedrock Dedicated Server (BDS) — first-class platform.

Bedrock is NOT a Java derivative. It uses Mojang's native dedicated server
binary, distributed as a zip from minecraft.net.

This module covers native BDS. Geyser/Floodgate (running Bedrock clients
against a Java server) lives in runtime/geyser.py and is layered on top of
the Java platforms, not here.

Resolution strategy:
  Mojang publishes a JSON feed of "what download URLs are current" that the
  launcher uses. We GET that, pick the linux-server entry, parse the version
  out of the filename. Bedrock is single-track — there is no historical
  version selection from this endpoint, just `latest`.

Notes:
  * The Mojang feed gates by User-Agent; default httpx UA gets a 403.
  * The download URL bakes the version into its filename:
        bedrock-server-1.21.51.02.zip
  * EULA acceptance is required at first run (handled by the Docker image
    via env var, not here).
  * No SHA published by Mojang; we can't verify like Paper does.
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import httpx

from .base import Family, InstallArtifact, Platform, VersionInfo

# The launcher uses this; it's the most stable JSON-native source.
BDS_LINKS_FEED = (
    "https://net-secondary.web.minecraft-services.net/api/v1.0/download/links"
)
LINUX_DOWNLOAD_TYPE = "serverBedrockLinux"

# Mojang blocks the default httpx UA with 403.
_UA = "Mozilla/5.0 (ndrchst; +https://github.com/asuramaya/ndrchst-alpha)"

_FILENAME_VERSION = re.compile(r"bedrock-server-([\d.]+)\.zip")


def _parse_version_from_url(url: str) -> str:
    m = _FILENAME_VERSION.search(url)
    if not m:
        raise ValueError(f"could not parse version from BDS url: {url}")
    return m.group(1)


class Bedrock(Platform):
    id = "bedrock"
    family = Family.BEDROCK
    display_name = "Bedrock"

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            # 300s read timeout: the zip is ~80 MB and we stream it; a slow
            # link or temporary CDN hiccup shouldn't fail the install.
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=15.0, read=300.0, write=60.0, pool=15.0),
                headers={"User-Agent": _UA},
            )
        return self._client

    async def _resolve_linux_download(self) -> str:
        client = await self._http()
        r = await client.get(BDS_LINKS_FEED)
        r.raise_for_status()
        data = r.json()
        links = data.get("result", {}).get("links", [])
        for link in links:
            if link.get("downloadType") == LINUX_DOWNLOAD_TYPE:
                return link["downloadUrl"]
        raise RuntimeError(
            f"{LINUX_DOWNLOAD_TYPE} entry missing from Mojang feed; schema may have drifted"
        )

    async def versions(self) -> list[VersionInfo]:
        url = await self._resolve_linux_download()
        version = _parse_version_from_url(url)
        return [VersionInfo(version=version, stable=True)]

    async def install(self, version: str, dest: Path) -> InstallArtifact:
        """Download + extract BDS. `version` is informational; Mojang only
        exposes the current build through this feed."""
        dest.mkdir(parents=True, exist_ok=True)
        client = await self._http()

        url = await self._resolve_linux_download()
        resolved_version = _parse_version_from_url(url)
        if version not in ("latest", resolved_version):
            # Soft warning via exception — caller picks the policy
            raise ValueError(
                f"requested bedrock {version} but Mojang only publishes "
                f"{resolved_version} from this endpoint"
            )

        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            buf = io.BytesIO()
            async for chunk in resp.aiter_bytes(chunk_size=128 * 1024):
                buf.write(chunk)

        buf.seek(0)
        with zipfile.ZipFile(buf) as zf:
            for member in zf.namelist():
                # zip slip guard
                target = (dest / member).resolve()
                if not str(target).startswith(str(dest.resolve())):
                    raise RuntimeError(f"refusing to extract outside dest: {member}")
            zf.extractall(dest)

        entrypoint = dest / "bedrock_server"
        if not entrypoint.exists():
            raise RuntimeError(
                "bedrock_server binary missing from extracted zip — Mojang layout changed?"
            )
        # BDS ships +x but some extract paths drop the mode bit
        entrypoint.chmod(0o755)
        return InstallArtifact(path=dest, entrypoint="bedrock_server")
