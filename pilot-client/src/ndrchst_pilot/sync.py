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

import concurrent.futures
import hashlib
import json
import urllib.error
import urllib.request
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# Mods come from CurseForge's global CDN, which happily serves many parallel
# connections — fetch them concurrently so the ~1.25 GB first sync saturates
# the link instead of crawling one file at a time.
_FETCH_WORKERS = 16

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
        # Already present + (client-only carries no sha1, else sha1 matches)
        # → cached, keep it. This is the incremental cache: re-syncs only
        # fetch what actually changed.
        if path.exists() and (
            meta.get("sha1") is None or _sha1_file(path) == meta["sha1"]
        ):
            kept += 1
            continue
        to_fetch.append(name)

    if not to_fetch and not to_remove:
        on_log(f"  {label}: already in sync ({kept} unchanged)")
        return SyncResult(added=0, replaced=0, removed=0, kept=kept)

    for name in to_remove:
        on_log(f"  {label}: removing {name} (not on server)")
        local_files[name].unlink()

    def _fetch(name: str) -> tuple[str, bool, bool, bool, Exception | None]:
        """Download one entry (CDN, with origin fallback). Returns
        (name, ok, used_origin, existed, error). Each entry writes its own
        .part→final file, so this is safe to run on a worker thread."""
        meta = wanted[name]
        target = dest / name
        existed = target.exists()
        url = abs_url(meta.get("url") or _origin_url(sync_base_url, name))
        # An origin fallback only exists when the server has its own copy
        # (mods/ substitutions). Client-only cosmetic assets have origin_url
        # None — don't pretend.
        origin = abs_url(meta["origin_url"]) if meta.get("origin_url") else None
        try:
            _http_download(url, target)
            return (name, True, not url.startswith(cdn_base), existed, None)
        except (urllib.error.URLError, OSError) as exc:
            err: Exception = exc
            if origin and origin != url:
                try:
                    _http_download(origin, target)
                    return (name, True, True, existed, None)
                except (urllib.error.URLError, OSError) as exc2:
                    err = exc2
            return (name, False, False, existed, err)

    added = replaced = failed = cdn_hits = origin_hits = 0
    fail_errs: list[tuple[str, Exception | None]] = []
    progress_every = max(len(to_fetch) // 10, 25)
    workers = max(1, min(_FETCH_WORKERS, len(to_fetch)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_fetch, n) for n in to_fetch]
        for done, fut in enumerate(concurrent.futures.as_completed(futures), start=1):
            name, ok, used_origin, existed, err = fut.result()
            if ok:
                origin_hits += used_origin
                cdn_hits += not used_origin
                replaced += existed
                added += not existed
            else:
                failed += 1
                fail_errs.append((name, err))
            if done % progress_every == 0 or done == len(to_fetch):
                extra = f", {failed} failed" if failed else ""
                on_log(f"  {label}: {done}/{len(to_fetch)} fetched "
                       f"({cdn_hits} from CDN, {origin_hits} from origin{extra})")

    # mods/ is load-bearing → a missing jar must abort the launch. Cosmetic
    # dirs are optional → log and carry on so a missing shaderpack never blocks.
    if failed and mirror:
        name, err = fail_errs[0]
        raise SyncError(f"failed to download {name}: {err}")
    for name, err in fail_errs:
        on_log(f"  {label}: skipping {name} (download failed: {err})")

    return SyncResult(
        added=added, replaced=replaced, removed=len(to_remove), kept=kept,
    )


def _origin_url(sync_base_url: str, filename: str) -> str:
    import urllib.parse
    return f"{sync_base_url}/mods/{urllib.parse.quote(filename, safe='')}"
