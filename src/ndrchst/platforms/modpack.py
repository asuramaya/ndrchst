"""Modpack platform — install an arbitrary modded server from a server-pack zip URL.

Pattern: the user picks "Modpack" in the create form and pastes a URL
to a `Server-Files-X.Y.Z.zip` (or equivalent). The platform downloads,
unzips, and runs whichever modloader installer is bundled inside.

The `version` field on this platform is the URL itself — not a SemVer
string. We accept HTTPS URLs only (no `file://`, no `http://` for the
public surface). Total download size is capped to protect ndrchst-01
from rogue links.

Layout we expect inside the zip, after unzipping into the data dir:
  - `run.sh`                                          (already-built NeoForge — done)
  - `neoforge-X.Y.Z-installer.jar`                    (run it)
  - `startserver.sh`                                  (CF convention; usually wraps the installer)
  - `forge-X.Y.Z-installer.jar` / `fabric-installer*` (future, not supported yet)

If none of the above are present we surface an error rather than ship
a half-installed server. Mojang EULA is still accepted by the lifecycle
layer (writes eula.txt) so the user doesn't see a friendly red error
on first boot.
"""
from __future__ import annotations

import asyncio
import re
import zipfile
from pathlib import Path

import httpx

from ..runtime.jvm_installer import JvmInstallError, run_jdk_jar
from .base import Family, InstallArtifact, Platform, VersionInfo

# Cap zip downloads at 4 GiB. Realistic NeoForge server packs are
# 1-2 GiB; anything dramatically larger is a smell.
MAX_PACK_BYTES = 4 * 1024 * 1024 * 1024


class ModpackInstallError(RuntimeError):
    pass


class Modpack(Platform):
    id = "modpack"
    family = Family.JAVA
    display_name = "Modpack (server-pack URL)"
    implemented = True
    default_visible = True

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(300.0, connect=30.0),
                follow_redirects=True,
            )
        return self._client

    async def versions(self) -> list[VersionInfo]:
        # No version listing — the "version" field is the user-supplied URL.
        return []

    async def install(self, version: str, dest: Path) -> InstallArtifact:
        url = (version or "").strip()
        if not url.startswith("https://"):
            raise ModpackInstallError(
                f"modpack version must be an https URL (got: {url!r})"
            )

        dest.mkdir(parents=True, exist_ok=True)
        zip_path = dest / "_server-pack.zip"

        # Stream the download with a size cap.
        client = await self._http()
        total = 0
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with zip_path.open("wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=256 * 1024):
                    total += len(chunk)
                    if total > MAX_PACK_BYTES:
                        zip_path.unlink(missing_ok=True)
                        raise ModpackInstallError(
                            f"server pack exceeded {MAX_PACK_BYTES // (1024**3)} GiB "
                            f"download cap before completing"
                        )
                    f.write(chunk)

        if not zipfile.is_zipfile(zip_path):
            zip_path.unlink(missing_ok=True)
            raise ModpackInstallError(
                "downloaded file is not a zip; check the URL "
                "(CurseForge sometimes returns an HTML interstitial)"
            )

        # Unzip in-place. Reject paths that try to escape the data dir.
        _safe_extract(zip_path, dest)
        zip_path.unlink(missing_ok=True)

        # Find a NeoForge installer jar to run, unless run.sh already exists
        # (some packs ship a pre-baked install).
        run_sh = dest / "run.sh"
        if not run_sh.exists():
            installer = _find_neoforge_installer(dest)
            if installer is None:
                contents = ", ".join(sorted(p.name for p in dest.iterdir())[:20])
                raise ModpackInstallError(
                    "no run.sh and no NeoForge installer.jar found inside the zip. "
                    f"Top-level contents: {contents}"
                )
            try:
                await asyncio.to_thread(
                    run_jdk_jar,
                    workdir=dest,
                    args=["-jar", installer.name, "--installServer", "/work"],
                )
            except JvmInstallError as e:
                raise ModpackInstallError(
                    f"bundled NeoForge installer failed: {e}"
                ) from e
            if not run_sh.exists():
                raise ModpackInstallError(
                    "NeoForge installer ran but didn't produce run.sh"
                )

        # Same memory-args neutering as the plain NeoForge install path.
        user_jvm = dest / "user_jvm_args.txt"
        if user_jvm.exists():
            user_jvm.write_text(
                "# Memory is set by ndrchst at container boot via JAVA_TOOL_OPTIONS.\n"
                "# Add server-specific JVM args via the Config tab.\n"
            )

        return InstallArtifact(path=dest, entrypoint="run.sh")


def _find_neoforge_installer(root: Path) -> Path | None:
    pattern = re.compile(r"^neoforge-[\d.]+-installer\.jar$")
    for path in sorted(root.iterdir()):
        if path.is_file() and pattern.match(path.name):
            return path
    return None


def _safe_extract(zip_path: Path, dest: Path) -> None:
    """Extract `zip_path` into `dest`, rejecting any entry that would
    escape the destination (zip-slip)."""
    dest_resolved = dest.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            target = (dest / info.filename).resolve()
            if dest_resolved != target and dest_resolved not in target.parents:
                raise ModpackInstallError(
                    f"zip entry would escape data dir: {info.filename}"
                )
        zf.extractall(dest)
