"""NeoForge platform — the modern Forge fork that's now the default for
modded MC 1.20.5+.

Version scheme: NeoForge versions look like `<MC_MAJOR_MINUS_ONE>.<MC_MINOR>.<PATCH>`.
So NeoForge `21.1.x` targets MC `1.21.1`, NeoForge `21.11.x` targets MC `1.21.11`.
Beta tags (`-beta` suffix) are filtered out of the default listing.

Install flow:
  1. Download `neoforge-<version>-installer.jar` from Maven into the data dir.
  2. Run `java -jar installer.jar --installServer /work` inside a one-shot
     eclipse-temurin:21-jdk container with the data dir mounted at /work.
     The installer materialises `libraries/`, `run.sh`, `user_jvm_args.txt`,
     and `unix_args.txt`.
  3. Rewrite `user_jvm_args.txt` (we set memory at boot from the cmdline,
     so leaving the installer's defaults causes a conflict).
  4. Accept the Mojang EULA on the user's behalf (handled by the eula
     module, called from lifecycle).

Boot: the container cmd becomes `bash run.sh nogui`. run.sh sources both
JVM args files and exec's java with `@-args` files. We inject `-Xmx/-Xms`
via the lifecycle-built cmd, which takes precedence over user_jvm_args.txt.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from ..runtime.jvm_installer import JvmInstallError, run_jdk_jar
from .base import Family, InstallArtifact, Platform, VersionInfo

NEOFORGE_MAVEN_LISTING = (
    "https://maven.neoforged.net/api/maven/versions/releases/net/neoforged/neoforge"
)
NEOFORGE_INSTALLER_URL = (
    "https://maven.neoforged.net/releases/net/neoforged/neoforge/"
    "{version}/neoforge-{version}-installer.jar"
)


def _is_stable(version: str) -> bool:
    return "-beta" not in version and "-alpha" not in version


def _sort_key(v: str) -> tuple[int, ...]:
    """Sort by numeric parts of the version; strips non-digit suffix tokens."""
    out: list[int] = []
    for part in v.split("."):
        # Trim non-numeric trailing junk (e.g. "0-rc1" → 0).
        n = ""
        for ch in part:
            if ch.isdigit():
                n += ch
            else:
                break
        if n:
            out.append(int(n))
    return tuple(out)


class NeoForge(Platform):
    id = "neoforge"
    family = Family.JAVA
    display_name = "NeoForge"
    implemented = True
    default_visible = True
    # Modloaders sit on top of vanilla MC and load hundreds of mods worth
    # of state. 4 GB is the practical floor; ATM10-class packs need 8+.
    # Default to 8192 so the create form lands on a number that works for
    # most packs without manual override.
    default_memory_mb = 8192

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60.0)
        return self._client

    async def versions(self) -> list[VersionInfo]:
        """Return stable NeoForge releases, newest first."""
        client = await self._http()
        r = await client.get(NEOFORGE_MAVEN_LISTING)
        r.raise_for_status()
        payload = r.json()
        raw = payload.get("versions") or []
        stable = [v for v in raw if _is_stable(v)]
        stable.sort(key=_sort_key, reverse=True)
        return [VersionInfo(version=v, stable=True) for v in stable]

    async def install(self, version: str, dest: Path) -> InstallArtifact:
        """Download the installer and run it in a JDK container, leaving
        a runnable NeoForge server at ``dest``."""
        dest.mkdir(parents=True, exist_ok=True)
        installer_path = dest / f"neoforge-{version}-installer.jar"
        url = NEOFORGE_INSTALLER_URL.format(version=version)

        client = await self._http()
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with installer_path.open("wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                    f.write(chunk)

        # docker-py is sync; offload the install run to a worker thread so
        # we don't block the FastAPI event loop while NeoForge resolves
        # libraries (can take 30-90s on a cold engine).
        try:
            await asyncio.to_thread(
                run_jdk_jar,
                workdir=dest,
                args=["-jar", installer_path.name, "--installServer", "/work"],
            )
        except JvmInstallError:
            # Surface up; lifecycle wraps in LifecycleError → clean 4xx
            raise

        # Memory is owned by the lifecycle layer (via -Xmx on the container
        # cmd) — wipe the installer's defaults so we don't end up with two
        # competing Xmx values when run.sh expands @user_jvm_args.txt.
        user_jvm_args = dest / "user_jvm_args.txt"
        if user_jvm_args.exists():
            user_jvm_args.write_text(
                "# Memory is set by ndrchst at container boot via -Xmx/-Xms.\n"
                "# Add server-specific JVM args via the Config tab.\n"
            )

        # The installer also drops a run.sh that's the canonical entrypoint.
        run_sh = dest / "run.sh"
        if not run_sh.exists():
            raise JvmInstallError(
                f"NeoForge installer did not produce run.sh in {dest}; "
                f"check the installer output. Files: {sorted(p.name for p in dest.iterdir())}"
            )

        return InstallArtifact(
            path=dest,
            entrypoint="run.sh",
            extra_files=(installer_path,),
        )
