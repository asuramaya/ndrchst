"""Server-driven asset sync.

The server's index is the canonical client asset set. The pilot pulls
it from <edge>/pilot/<sid>/mods/index.json and mirrors it locally. Each
index entry carries a ``target`` dir (default ``mods``) telling the
pilot where the file loads from on the client:

  - ``mods``        → <profile>/mods/        (jars; server-authoritative)
  - ``shaderpacks`` → <profile>/shaderpacks/ (shader .zips)
  - ``resourcepacks`` → <profile>/resourcepacks/
  - ``saves``       → <profile>/saves/

For mods/ we mirror exactly (add missing, replace on sha1 mismatch,
prune anything the server dropped). For the cosmetic dirs we're
additive — we ensure the pack's files are present but never delete a
user's own shaderpacks/resourcepacks.

This replaces resolving from the CurseForge manifest: upstream manifest
rot (deleted file IDs, swapped projects) doesn't matter because the
operator's curated set on the server is authoritative — substitutions
made server-side propagate to every client on its next sync.
"""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# Dirs we mirror exactly (prune extras). Everything else is additive —
# we add the pack's files but leave the user's own additions alone.
_MIRRORED_DIRS = {"mods"}


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


def sync_assets_from_server(
    *,
    sync_base_url: str,
    profile_dir: Path,
    on_log: Callable[[str], None],
) -> SyncResult:
    """Bring the local client asset set in line with the server's index.

    Each index entry carries a ``target`` dir (default ``mods``). We
    group by target and sync each into ``profile_dir/<target>/``:

      - ``mods`` is mirrored exactly: add/replace by sha1, prune extras.
      - cosmetic dirs (shaderpacks, resourcepacks, saves) are additive:
        we ensure the pack's files are present but never prune.

    Bytes come from each entry's CDN URL (edge.forgecdn.net — global,
    fast, off the operator's uplink) with an origin fallback for the
    handful of substitutions or CDN failures. This is what scales to
    hundreds of users.

    `sync_base_url` should NOT include a trailing slash; it's typically
    `https://play.ndrchst.com/pilot/<sid>`."""
    index_url = f"{sync_base_url}/mods/index.json"
    on_log(f"Fetching server asset index from {index_url}…")
    try:
        payload = _http_get_json(index_url)
    except SyncError as e:
        raise SyncError(f"failed to read server asset index: {e}") from e

    by_target: dict[str, dict[str, dict]] = defaultdict(dict)
    for m in payload.get("mods", []):
        by_target[m.get("target") or "mods"][m["filename"]] = m
    summary = ", ".join(f"{len(v)} {k}" for k, v in sorted(by_target.items()))
    on_log(f"Server index: {summary}")

    import urllib.parse as _up
    cdn_base = "https://edge.forgecdn.net"
    parsed = _up.urlsplit(sync_base_url)
    site_origin = f"{parsed.scheme}://{parsed.netloc}"

    def _abs(u: str) -> str:
        return u if u.startswith("http") else _up.urljoin(site_origin, u)

    total = SyncResult(added=0, replaced=0, removed=0, kept=0)
    for target, wanted in sorted(by_target.items()):
        dest = profile_dir / target
        dest.mkdir(parents=True, exist_ok=True)
        mirror = target in _MIRRORED_DIRS
        res = _sync_dir(
            wanted=wanted, dest=dest, mirror=mirror, label=target,
            abs_url=_abs, cdn_base=cdn_base, sync_base_url=sync_base_url,
            on_log=on_log,
        )
        total = SyncResult(
            added=total.added + res.added,
            replaced=total.replaced + res.replaced,
            removed=total.removed + res.removed,
            kept=total.kept + res.kept,
        )
    return total


# Back-compat alias: the function used to sync only mods/.
sync_mods_from_server = sync_assets_from_server


def _sync_dir(
    *,
    wanted: dict[str, dict],
    dest: Path,
    mirror: bool,
    label: str,
    abs_url: Callable[[str], str],
    cdn_base: str,
    sync_base_url: str,
    on_log: Callable[[str], None],
) -> SyncResult:
    """Sync one target directory. If `mirror`, prune local files the
    server no longer lists; otherwise leave extras alone (additive)."""
    # Only consider files that look like the assets we manage. mods/ are
    # jars; cosmetic dirs are zips. Pruning a user's screenshots/ would be
    # rude, so we scope "extras" to the relevant extension.
    exts = (".jar",) if label == "mods" else (".zip",)
    local_files = {
        p.name: p for p in dest.iterdir()
        if p.is_file() and p.name.endswith(exts)
    }

    to_remove = [n for n in local_files if n not in wanted] if mirror else []
    to_fetch = []
    kept = 0
    for name, meta in wanted.items():
        path = dest / name
        if path.exists():
            # Client-only assets carry no sha1 (no server-side copy to
            # hash) — presence is enough. sha1'd entries are verified.
            if meta.get("sha1") is None or _sha1_file(path) == meta["sha1"]:
                kept += 1
                continue
        to_fetch.append(name)

    if not to_fetch and not to_remove:
        on_log(f"  {label}: already in sync ({kept} unchanged)")
        return SyncResult(added=0, replaced=0, removed=0, kept=kept)

    for name in to_remove:
        on_log(f"  {label}: removing {name} (not on server)")
        local_files[name].unlink()

    added = replaced = failed = 0
    cdn_hits = origin_hits = 0
    progress_every = max(len(to_fetch) // 10, 25)
    last_logged = 0
    for i, name in enumerate(to_fetch, start=1):
        meta = wanted[name]
        target = dest / name
        existed = target.exists()
        origin_raw = meta.get("origin_url") or _origin_url(sync_base_url, name)
        url = abs_url(meta.get("url") or origin_raw)
        # An origin fallback only exists when the server has its own copy
        # (mods/ substitutions). Client-only cosmetic assets have no
        # origin copy — the index gives origin_url=None — so don't pretend.
        origin = abs_url(meta["origin_url"]) if meta.get("origin_url") else None
        try:
            _http_download(url, target)
            if url.startswith(cdn_base):
                cdn_hits += 1
            else:
                origin_hits += 1
        except (urllib.error.URLError, OSError) as exc:
            # CDN URL failed (e.g. 403 third-party-disabled) — fall back
            # to the operator's origin copy if there is a distinct one.
            err: Exception = exc
            recovered = False
            if origin and origin != url:
                try:
                    _http_download(origin, target)
                    origin_hits += 1
                    recovered = True
                except (urllib.error.URLError, OSError) as exc2:
                    err = exc2
            if not recovered:
                # mods/ is load-bearing → abort. Cosmetic dirs are
                # optional → log and keep going so a missing shaderpack
                # never blocks the launch.
                if mirror:
                    raise SyncError(f"failed to download {name} from {url}") from err
                failed += 1
                on_log(f"  {label}: skipping {name} (download failed: {err})")
                continue
        replaced += 1 if existed else 0
        added += 0 if existed else 1
        if i - last_logged >= progress_every or i == len(to_fetch):
            extra = f", {failed} failed" if failed else ""
            on_log(
                f"  {label}: {i}/{len(to_fetch)} fetched "
                f"({cdn_hits} from CDN, {origin_hits} from origin{extra})"
            )
            last_logged = i

    return SyncResult(
        added=added, replaced=replaced, removed=len(to_remove), kept=kept,
    )


def _origin_url(sync_base_url: str, filename: str) -> str:
    import urllib.parse
    return f"{sync_base_url}/mods/{urllib.parse.quote(filename, safe='')}"
