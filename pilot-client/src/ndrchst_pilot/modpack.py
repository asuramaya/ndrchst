"""Client-side modpack install.

The server-side platform `runtime/modpack.py` handles **server** packs.
This module is the client analogue: given a CurseForge client pack
(manifest.json + overrides/), install:

  - All mods listed in manifest.files, fetched via the vendored CF
    resolver (same code path the server uses).
  - The pack's overrides/ tree (configs, kubejs scripts, etc.) applied
    over the destination directory.

Caller integrates with portablemc's profile dir layout — the mods go
into <ctx>/<profile>/mods/, overrides into <ctx>/<profile>/.

We tolerate a small failure ratio (manifest rot) the same way the
server-side does; the operator gets a sidecar file listing what was
missed.
"""
from __future__ import annotations

import asyncio
import logging
import zipfile
from pathlib import Path
from typing import Callable

import httpx

from . import curseforge as cf

log = logging.getLogger("ndrchst_pilot.modpack")

# Same tolerance as the server side — kitchen-sink packs lose 1-2 file
# IDs to author deletion over their lifetime. More than 5% is broken
# enough that the operator should pick a different pack version.
MODPACK_FAILURE_TOLERANCE = 0.05


class ModpackInstallError(RuntimeError):
    pass


def fetch_modpack_zip(
    url: str, dest_zip: Path, *, on_log: Callable[[str], None],
) -> None:
    """Download a modpack zip (sync, urllib-based to avoid an async dep
    just for one download). Skips the download if a valid zip is already
    cached at `dest_zip` — install can be safely re-run after a crash."""
    import urllib.request
    dest_zip.parent.mkdir(parents=True, exist_ok=True)

    # Cached? Skip download.
    if dest_zip.exists() and zipfile.is_zipfile(dest_zip):
        on_log(
            f"Modpack zip already cached at {dest_zip} "
            f"({dest_zip.stat().st_size / 1e6:.1f} MB) — skipping download"
        )
        return

    on_log(f"Downloading modpack from {url}…")
    tmp = dest_zip.with_suffix(dest_zip.suffix + ".part")
    # CF's CDN rejects Python's default urllib UA with 403; spoof a
    # browser-ish UA the way every other modpack launcher does.
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (ndrchst-pilot)"},
    )
    try:
        with urllib.request.urlopen(req) as resp, tmp.open("wb") as f:
            total_len = int(resp.headers.get("content-length") or 0)
            total = 0
            last_emit = 0
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                total += len(chunk)
                if total - last_emit >= 16 * 1024 * 1024:
                    pct = (total / total_len * 100) if total_len else 0
                    on_log(f"  {total/1e6:.0f} MB / {total_len/1e6:.0f} MB ({pct:.0f}%)")
                    last_emit = total
        if not zipfile.is_zipfile(tmp):
            tmp.unlink(missing_ok=True)
            raise ModpackInstallError(
                "downloaded file is not a zip — check the URL"
            )
        tmp.rename(dest_zip)
        on_log(f"Downloaded {total / 1e6:.1f} MB")
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


async def _async_install(
    *,
    pack_zip: Path,
    profile_dir: Path,
    on_log: Callable[[str], None],
) -> tuple[int, int]:
    """Returns (successes, failures). Raises ModpackInstallError if the
    failure ratio is above tolerance."""
    manifest = cf.read_manifest(pack_zip)
    on_log(
        f"Pack: {manifest.name} v{manifest.version} "
        f"(MC {manifest.mc_version}, {manifest.loader_id}, {len(manifest.files)} mods)"
    )
    mods_dir = profile_dir / "mods"
    async with httpx.AsyncClient(
        timeout=60.0,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (ndrchst-pilot)"},
    ) as client:
        last_logged = 0

        def progress(done: int, total: int) -> None:
            nonlocal last_logged
            if done == total or done - last_logged >= 25:
                on_log(f"  mod download {done}/{total}")
                last_logged = done

        successes, failures = await cf.download_all_mods(
            client, manifest.files, mods_dir, on_progress=progress,
        )

    if failures:
        ratio = len(failures) / max(len(manifest.files), 1)
        for entry, err in failures:
            log.warning("mod download failed: %s/%s: %s",
                        entry.project_id, entry.file_id, err)
        on_log(
            f"⚠ {len(failures)} of {len(manifest.files)} mods could not be "
            f"downloaded ({ratio:.1%}); details in "
            "ndrchst-missing-mods.txt"
        )
        missing_path = profile_dir / "ndrchst-missing-mods.txt"
        missing_path.write_text(
            f"# {len(failures)} mods from this modpack's manifest could "
            "not be downloaded.\n"
            "# Manifest entries (projectID/fileID) and the reason:\n\n"
            + "\n".join(
                f"{e.project_id}/{e.file_id}\t{err}"
                for e, err in failures
            )
            + "\n",
        )
        if ratio > MODPACK_FAILURE_TOLERANCE:
            raise ModpackInstallError(
                f"{len(failures)} of {len(manifest.files)} mods failed to "
                f"download ({ratio:.1%} > "
                f"{MODPACK_FAILURE_TOLERANCE:.0%} threshold)"
            )

    # Apply overrides on top of the profile dir.
    on_log("Applying override files…")
    n = cf.apply_overrides(pack_zip, profile_dir, manifest.overrides_dir)
    on_log(f"Applied {n} override files")
    return len(successes), len(failures)


def install_client_pack(
    *,
    url: str,
    profile_dir: Path,
    on_log: Callable[[str], None],
) -> tuple[int, int]:
    """Sync wrapper for the async install. Downloads the pack, resolves
    every mod via CurseForge, applies overrides. Returns
    (mods_installed, mods_failed)."""
    pack_zip = profile_dir / "_modpack.zip"
    fetch_modpack_zip(url, pack_zip, on_log=on_log)
    # Keep the zip around — fetch_modpack_zip's cache check will skip
    # re-downloading on subsequent installs.
    return asyncio.run(_async_install(
        pack_zip=pack_zip, profile_dir=profile_dir, on_log=on_log,
    ))
