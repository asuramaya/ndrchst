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
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


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
    """Bring the local mods set in line with the server's.

    Strategy:
      1. GET <base>/mods/index.json → the server's authoritative set
         (filename → sha1).
      2. If local already matches exactly, do nothing.
      3. If anything differs, pull the whole set as one mods.zip (one
         request runs at full tunnel bandwidth; 450 individual requests
         are dominated by per-request overhead) and extract, then prune
         anything the server no longer has.

    `sync_base_url` should NOT include a trailing slash; it's typically
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

    local_files = {
        p.name: p for p in mods_dir.iterdir()
        if p.is_file() and p.name.endswith(".jar")
    }

    # What needs to change?
    to_remove = [n for n in local_files if n not in server_mods]
    to_fetch = []
    kept = 0
    for name, meta in server_mods.items():
        path = mods_dir / name
        if path.exists() and _sha1_file(path) == meta["sha1"]:
            kept += 1
        else:
            to_fetch.append(name)

    if not to_fetch and not to_remove:
        on_log(f"Mods already in sync ({kept} unchanged)")
        return SyncResult(added=0, replaced=0, removed=0, kept=kept)

    # Prune extras first.
    for name in to_remove:
        on_log(f"  removing {name} (not on server)")
        local_files[name].unlink()

    # Download each mod from its URL. The index gives a CDN URL
    # (edge.forgecdn.net) for most mods — global, fast, doesn't touch the
    # operator's uplink — and an origin fallback (our server) for the
    # handful of substitutions or CDN failures. This is what scales to
    # hundreds of users.
    import urllib.parse as _up
    cdn_base = "https://edge.forgecdn.net"
    # Origin for resolving relative URLs (the live-fallback index uses
    # paths like "/pilot/<sid>/mods/<file>").
    parsed = _up.urlsplit(sync_base_url)
    site_origin = f"{parsed.scheme}://{parsed.netloc}"

    def _abs(u: str) -> str:
        return u if u.startswith("http") else _up.urljoin(site_origin, u)

    added = replaced = 0
    cdn_hits = origin_hits = 0
    progress_every = max(len(to_fetch) // 10, 25)
    last_logged = 0
    for i, name in enumerate(to_fetch, start=1):
        meta = server_mods[name]
        target = mods_dir / name
        existed = target.exists()
        url = _abs(meta.get("url") or _origin_url(sync_base_url, name))
        origin = _abs(meta.get("origin_url") or _origin_url(sync_base_url, name))
        try:
            _http_download(url, target)
            if url.startswith(cdn_base):
                cdn_hits += 1
            else:
                origin_hits += 1
        except (urllib.error.URLError, OSError):
            # CDN URL failed (e.g. 403 third-party-disabled) — fall back
            # to the operator's origin copy.
            if url != origin:
                _http_download(origin, target)
                origin_hits += 1
            else:
                raise SyncError(f"failed to download {name} from {url}")
        replaced += 1 if existed else 0
        added += 0 if existed else 1
        if i - last_logged >= progress_every or i == len(to_fetch):
            on_log(
                f"  {i}/{len(to_fetch)} fetched "
                f"({cdn_hits} from CDN, {origin_hits} from origin)"
            )
            last_logged = i

    return SyncResult(
        added=added, replaced=replaced, removed=len(to_remove), kept=kept,
    )


def _origin_url(sync_base_url: str, filename: str) -> str:
    import urllib.parse
    return f"{sync_base_url}/mods/{urllib.parse.quote(filename, safe='')}"
