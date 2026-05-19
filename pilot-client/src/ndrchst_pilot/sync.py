"""Server-driven mod sync.

The server's mods/ directory is the canonical mod set. The pilot pulls
an index from <edge>/pilot/<sid>/mods/index.json and mirrors it locally:

  - download any jar missing locally
  - replace any jar whose sha1 doesn't match the server's
  - delete any local jar the server no longer has

This replaces resolving mods from the CurseForge manifest. Upstream
manifest rot (deleted file IDs, swapped projects) doesn't matter
because the operator's curated set on the server is authoritative —
substitutions made server-side propagate to every client install on
its next sync.
"""
from __future__ import annotations

import hashlib
import urllib.request
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import json


class SyncError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SyncResult:
    added: int
    replaced: int
    removed: int
    kept: int


_UA = "Mozilla/5.0 (ndrchst-pilot)"


def _sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _http_get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise SyncError(f"GET {url} returned HTTP {e.code}: {e.reason}") from e
    except (OSError, json.JSONDecodeError) as e:
        raise SyncError(f"GET {url} failed: {e}") from e


def _http_download(url: str, dest: Path) -> None:
    """Stream a URL to disk via a .part temp + rename."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp, tmp.open("wb") as f:
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
        tmp.rename(dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def sync_mods_from_server(
    *,
    sync_base_url: str,
    mods_dir: Path,
    on_log: Callable[[str], None],
) -> SyncResult:
    """Pull `<sync_base_url>/mods/index.json`, diff against `mods_dir`,
    and bring the local set in line with the server's. `sync_base_url`
    should NOT include the trailing slash; it's typically
    `https://play.ndrchst.com/pilot/<sid>`."""
    mods_dir.mkdir(parents=True, exist_ok=True)
    index_url = f"{sync_base_url}/mods/index.json"
    on_log(f"Fetching server mod index from {index_url}…")
    try:
        payload = _http_get_json(index_url)
    except SyncError as e:
        raise SyncError(f"failed to read server mod index: {e}") from e
    server_mods = {m["filename"]: m for m in payload.get("mods", [])}
    on_log(f"Server has {len(server_mods)} mods")

    # Snapshot local
    local_files = {
        p.name: p for p in mods_dir.iterdir()
        if p.is_file() and p.name.endswith(".jar")
    }

    added = replaced = removed = kept = 0
    # Remove anything the server doesn't have. Keep `.jar.disabled` files;
    # the operator may have disabled local mods deliberately.
    for name, path in local_files.items():
        if name not in server_mods:
            on_log(f"  removing {name} (not on server)")
            path.unlink()
            removed += 1

    # Add / replace
    progress_every = max(len(server_mods) // 10, 25)
    last_logged = 0
    for i, (name, meta) in enumerate(server_mods.items(), start=1):
        target = mods_dir / name
        need = True
        if target.exists():
            local_sha = _sha1_file(target)
            if local_sha == meta["sha1"]:
                kept += 1
                need = False
            else:
                replaced += 1
        else:
            added += 1
        if need:
            url = f"{sync_base_url}/mods/{name}"
            try:
                _http_download(url, target)
            except (urllib.error.URLError, OSError) as e:
                raise SyncError(
                    f"failed to download {name} from {url}: {e}"
                ) from e
        if i - last_logged >= progress_every or i == len(server_mods):
            on_log(
                f"  {i}/{len(server_mods)} synced "
                f"(+{added} new, ~{replaced} updated, ={kept} unchanged, -{removed} removed)"
            )
            last_logged = i

    return SyncResult(added=added, replaced=replaced, removed=removed, kept=kept)
