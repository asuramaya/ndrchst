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

import logging
import zipfile
from pathlib import Path
from typing import Callable

from . import curseforge as cf

log = logging.getLogger("ndrchst_pilot.modpack")


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


def _read_manifest_summary(pack_zip: Path) -> cf.ManifestSummary:
    """Bare-minimum: just so the operator sees the pack name + MC version
    in the log. Mod sync is server-driven; we don't resolve from manifest."""
    return cf.read_manifest(pack_zip)


def install_client_pack(
    *,
    url: str,
    profile_dir: Path,
    on_log: Callable[[str], None],
    sync_base_url: str | None = None,
) -> tuple[int, int]:
    """Install path:
      1. Download the CurseForge client-pack zip (cached if already present).
      2. Extract overrides/ on top of `profile_dir` — configs, kubejs
         scripts, defaultconfigs.
      3. If `sync_base_url` is provided, sync mods/ from the server (the
         authoritative source); otherwise leave mods/ empty for the
         operator to populate.

    Mods deliberately come from the server, not from the manifest. The
    operator's curated set wins over upstream rot.

    Returns (mods_synced, override_files_applied)."""
    pack_zip = profile_dir / "_modpack.zip"
    fetch_modpack_zip(url, pack_zip, on_log=on_log)

    manifest = _read_manifest_summary(pack_zip)
    on_log(
        f"Pack: {manifest.name} v{manifest.version} "
        f"(MC {manifest.mc_version}, {manifest.loader_id})"
    )

    on_log("Applying override files…")
    n_overrides = cf.apply_overrides(pack_zip, profile_dir, manifest.overrides_dir)
    on_log(f"Applied {n_overrides} override files")

    n_mods = 0
    if sync_base_url:
        on_log("Syncing mods from server (server is source of truth)…")
        from .sync import sync_mods_from_server
        try:
            result = sync_mods_from_server(
                sync_base_url=sync_base_url,
                mods_dir=profile_dir / "mods",
                on_log=on_log,
            )
            on_log(
                f"Mod sync complete: +{result.added} new, "
                f"~{result.replaced} updated, ={result.kept} unchanged, "
                f"-{result.removed} removed"
            )
            n_mods = result.added + result.replaced + result.kept
        except Exception as e:
            on_log(f"Mod sync failed: {e}")
            raise

    return n_mods, n_overrides
