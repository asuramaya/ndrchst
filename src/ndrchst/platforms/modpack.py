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
import logging
import re
import zipfile
from pathlib import Path

import httpx

from ..runtime import curseforge as cf_mod
from ..runtime.jvm_installer import JvmInstallError, run_jdk_jar
from .base import Family, InstallArtifact, Platform, VersionInfo

log = logging.getLogger("ndrchst.modpack")

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
    # Modpacks are NeoForge-flavoured by default; same RAM floor reasoning.
    default_memory_mb = 8192

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
        if not _accept_url(url):
            raise ModpackInstallError(
                f"modpack version must be an https URL (or http://127.0.0.1 / "
                f"http://localhost for an operator-side proxy), got: {url!r}"
            )

        dest.mkdir(parents=True, exist_ok=True)
        zip_path = dest / "_server-pack.zip"

        # Stream the download with a size cap.
        client = await self._http()

        # If the URL looks like a CurseForge modpack file with a published
        # server pack, swap it out before downloading — the server pack
        # is what the user actually wants, and it skips the whole
        # client-manifest resolve dance.
        resolved_url, swap_note = await cf_mod.resolve_to_server_pack(client, url)
        if swap_note:
            log.info("modpack URL %s -> %s (%s)", url, resolved_url, swap_note)
            url = resolved_url
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

        # Branch: is this a CurseForge client pack (manifest.json at the
        # root) or a self-contained server pack?
        with zipfile.ZipFile(zip_path) as zf:
            top_names = zf.namelist()
        is_cf_client_pack = "manifest.json" in top_names

        if is_cf_client_pack:
            await self._install_cf_client_pack(zip_path, dest, client)
        else:
            await self._install_server_pack(zip_path, dest)

        zip_path.unlink(missing_ok=True)

        # Same memory-args neutering as the plain NeoForge install path.
        _neuter_user_jvm_args(dest)

        return InstallArtifact(path=dest, entrypoint="run.sh")

    async def _install_server_pack(self, zip_path: Path, dest: Path) -> None:
        """Self-contained server pack flow: unzip, find a NeoForge
        installer (or trust an existing run.sh), and we're done."""
        _safe_extract(zip_path, dest)

        run_sh = dest / "run.sh"
        if run_sh.exists():
            return  # pre-baked, done

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

    async def _install_cf_client_pack(
        self, zip_path: Path, dest: Path, http_client: httpx.AsyncClient,
    ) -> None:
        """CurseForge client pack flow:
          1. Read manifest.json to learn the NeoForge version + mod list.
          2. Download the NeoForge installer for that version and run it
             (produces run.sh, libraries/, etc.).
          3. Resolve every {projectID, fileID} in manifest.files via the
             public CF v1 endpoint + edge.forgecdn.net CDN, dropping the
             jars in mods/.
          4. Extract overrides/* over the data dir (configs, kubejs scripts,
             pack-specific tuning).
        """
        manifest = cf_mod.read_manifest(zip_path)
        log.info(
            "Installing CF client pack %s v%s (MC %s, %s, %d mods)",
            manifest.name, manifest.version, manifest.mc_version,
            manifest.loader_id, len(manifest.files),
        )

        # NeoForge install matching the version the pack pins.
        nf_installer_name = f"neoforge-{manifest.loader_version}-installer.jar"
        nf_installer_path = dest / nf_installer_name
        nf_url = (
            "https://maven.neoforged.net/releases/net/neoforged/neoforge/"
            f"{manifest.loader_version}/{nf_installer_name}"
        )
        async with http_client.stream("GET", nf_url) as resp:
            resp.raise_for_status()
            with nf_installer_path.open("wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                    f.write(chunk)
        try:
            await asyncio.to_thread(
                run_jdk_jar,
                workdir=dest,
                args=["-jar", nf_installer_name, "--installServer", "/work"],
            )
        except JvmInstallError as e:
            raise ModpackInstallError(
                f"NeoForge {manifest.loader_version} installer failed: {e}"
            ) from e
        if not (dest / "run.sh").exists():
            raise ModpackInstallError(
                "NeoForge installer ran but didn't produce run.sh"
            )

        # Download every mod in parallel. Log progress every 25 completions
        # so a long install isn't silent.
        mods_dir = dest / "mods"
        last_logged = 0

        def progress(done: int, total: int) -> None:
            nonlocal last_logged
            if done == total or done - last_logged >= 25:
                log.info("mod download %d/%d", done, total)
                last_logged = done

        successes, failures = await cf_mod.download_all_mods(
            http_client, manifest.files, mods_dir, on_progress=progress,
        )
        if failures:
            # Modpack manifests rot — author deletes a mod, fileIDs go 404.
            # Server is the source of truth for mods (clients sync from the
            # server's mods/ dir at install time, and the operator can hand-
            # fix individual jars). So we surface the full failure list to
            # the operator but don't abort — let them fix specific mods after
            # the install lands. Server-side sync to clients ensures the
            # post-fix state propagates to every client.
            for entry, err in failures:
                log.warning("mod download failed: %s/%s: %s",
                            entry.project_id, entry.file_id, err)
            log.warning(
                "modpack install continuing with %d of %d mods missing — "
                "operator must drop the missing jars into mods/ by hand; "
                "details in ndrchst-missing-mods.txt",
                len(failures), len(manifest.files),
            )
            missing_path = dest / "ndrchst-missing-mods.txt"
            missing_path.write_text(
                f"# {len(failures)} mods from this modpack's manifest could not "
                "be downloaded.\n"
                "# Drop the jars into mods/ manually; clients will pick them up\n"
                "# automatically via the server-driven mod sync on next launch.\n"
                "# Manifest entries (projectID/fileID) and the reason:\n\n"
                + "\n".join(
                    f"{e.project_id}/{e.file_id}\t{err}"
                    for e, err in failures
                )
                + "\n",
            )
        log.info("downloaded %d mods to %s", len(successes), mods_dir)

        # Apply overrides/ on top of the data dir last so pack-specific
        # configs win over any defaults.
        try:
            n = cf_mod.apply_overrides(zip_path, dest, manifest.overrides_dir)
            log.info("applied %d override files", n)
        except cf_mod.CurseForgeError as e:
            raise ModpackInstallError(f"override extraction failed: {e}") from e

        _neuter_user_jvm_args(dest)


def _accept_url(url: str) -> bool:
    """https:// is the normal case. We also allow http://127.0.0.1 and
    http://localhost so the operator can spin up a local http.server to
    serve a zip they already have on the box — saves piping a 200 MB
    file through multipart upload. Plaintext http to other hosts stays
    rejected (credentials leak risk in prod)."""
    if url.startswith("https://"):
        return True
    return url.startswith(("http://127.0.0.1", "http://localhost"))


def _neuter_user_jvm_args(dest: Path) -> None:
    """Strip the installer's (and any pack's) -Xmx/-Xms from user_jvm_args.txt
    so the value we set via JAVA_TOOL_OPTIONS at boot is the only one in
    play. Other JVM flags inside the file (G1GC settings, etc.) are kept
    — modpacks often ship recommended GC tuning that's worth respecting."""
    user_jvm = dest / "user_jvm_args.txt"
    if not user_jvm.exists():
        return
    keep: list[str] = []
    for raw in user_jvm.read_text().splitlines():
        stripped = raw.strip()
        if stripped.startswith(("-Xmx", "-Xms")):
            continue
        keep.append(raw)
    body = "\n".join(keep).rstrip()
    if body:
        body += "\n\n"
    user_jvm.write_text(
        body
        + "# Memory is set by ndrchst at container boot via JAVA_TOOL_OPTIONS.\n"
        + "# Add server-specific JVM args via the Config tab.\n"
    )


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
