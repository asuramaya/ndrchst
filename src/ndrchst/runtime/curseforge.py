"""CurseForge manifest resolver.

CF distributes modpacks in two formats:
  - **Server packs** (`Server-Files-X.Y.Z.zip`) — pre-bundled mods/ +
    config/ + a NeoForge installer + startserver.sh. Operator just runs.
  - **Client packs** (`<PackName>-X.Y.Z.zip`) — `manifest.json` listing
    `{projectID, fileID}` tuples + an `overrides/` directory. The CF
    launcher resolves each tuple to a download URL at install time.

This module handles the client-pack case. We avoid the official CF API
(which needs a free-but-mandatory API key) by using two public surfaces
the CF website itself relies on:

  - ``https://www.curseforge.com/api/v1/mods/<projectId>/files/<fileId>``
    returns file metadata (notably `fileName`). No auth.
  - ``https://edge.forgecdn.net/files/<id1>/<id2>/<filename>`` is the
    public CDN for any mod's primary file. Pad rule: split the integer
    fileId into a 4-digit prefix and a remainder, no leading zero
    stripping. For ``7471280`` → ``7471/280``.

Caveats:

  - Mod authors can flag "third-party downloads disabled." For those
    files the CDN returns 403. We surface a clean per-mod error in that
    case so the operator knows which jar needs a manual fetch.
  - The unofficial v1 API isn't a stability contract — CF can change it.
    The cost when it breaks: this resolver throws clean errors and the
    operator falls back to the server-pack flow until we adapt.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger("ndrchst.curseforge")

CF_WEBSITE_API = "https://www.curseforge.com/api/v1/mods"
CF_CDN_BASE = "https://edge.forgecdn.net/files"

# Default concurrency for the parallel mod-download phase. A typical
# kitchen-sink modpack has 400-600 mods at a few hundred KB to a few MB
# each. 16 is enough to saturate a residential link without melting CF's
# CDN or hitting their rate limit (anecdotal: hundreds of files/min ok).
DEFAULT_PARALLEL = 16


class CurseForgeError(RuntimeError):
    """Wraps any failure resolving / fetching a CF asset."""


@dataclass(frozen=True, slots=True)
class CFEntry:
    """One `files[]` entry from a client-pack manifest."""
    project_id: int
    file_id: int
    required: bool

    @classmethod
    def from_manifest_entry(cls, raw: dict) -> CFEntry:
        return cls(
            project_id=int(raw["projectID"]),
            file_id=int(raw["fileID"]),
            required=bool(raw.get("required", True)),
        )


@dataclass(frozen=True, slots=True)
class ManifestSummary:
    name: str
    version: str
    mc_version: str
    loader_id: str            # e.g. "neoforge-21.1.228"
    loader_version: str       # the part after "neoforge-"
    files: tuple[CFEntry, ...]
    overrides_dir: str        # path inside the zip, usually "overrides"


def read_manifest(zip_path: Path) -> ManifestSummary:
    """Read + validate `manifest.json` from a client-pack zip.

    Raises CurseForgeError if the zip doesn't look like a client pack
    (no top-level manifest.json or wrong schema).
    """
    try:
        with zipfile.ZipFile(zip_path) as zf:
            if "manifest.json" not in zf.namelist():
                raise CurseForgeError(
                    "zip has no manifest.json — looks like a server pack "
                    "(unzip directly) rather than a CF client pack"
                )
            data = json.loads(zf.read("manifest.json").decode("utf-8"))
    except zipfile.BadZipFile as e:
        raise CurseForgeError(f"not a valid zip: {e}") from e
    except json.JSONDecodeError as e:
        raise CurseForgeError(f"manifest.json isn't valid JSON: {e}") from e

    try:
        mc_block = data["minecraft"]
        loaders = mc_block["modLoaders"]
        primary = loaders[0]
        loader_id = primary["id"]
        # `neoforge-21.1.228` → version `21.1.228`
        loader_name, _, loader_version = loader_id.partition("-")
        if loader_name != "neoforge":
            raise CurseForgeError(
                f"this resolver only supports NeoForge packs; got "
                f"loader='{loader_name}' in manifest"
            )
        entries = tuple(
            CFEntry.from_manifest_entry(r) for r in data.get("files", [])
        )
    except (KeyError, IndexError, TypeError) as e:
        raise CurseForgeError(
            f"manifest.json missing required fields ({e})"
        ) from e

    return ManifestSummary(
        name=data.get("name", "(unknown)"),
        version=data.get("version", "(unknown)"),
        mc_version=mc_block.get("version", "(unknown)"),
        loader_id=loader_id,
        loader_version=loader_version,
        files=entries,
        overrides_dir=data.get("overrides", "overrides"),
    )


def apply_overrides(zip_path: Path, dest: Path, overrides_dir: str) -> int:
    """Extract `<zip>/<overrides_dir>/*` into `dest`, returning the number
    of files written. Rejects path-traversal entries (zip-slip)."""
    prefix = overrides_dir.rstrip("/") + "/"
    dest_resolved = dest.resolve()
    count = 0
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if not info.filename.startswith(prefix):
                continue
            rel = info.filename[len(prefix):]
            if not rel:  # the directory entry itself
                continue
            target = (dest / rel).resolve()
            if dest_resolved != target and dest_resolved not in target.parents:
                raise CurseForgeError(
                    f"override entry would escape data dir: {info.filename}"
                )
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
            count += 1
    return count


def _cdn_url(file_id: int, filename: str) -> str:
    """The path split CF uses for their CDN. fileId 7471280 → 7471/280."""
    s = str(file_id)
    if len(s) <= 4:
        # Old, pre-2018-ish fileIds — never seen in modern packs but here
        # for completeness. Split is /0/<id>.
        return f"{CF_CDN_BASE}/0/{int(s)}/{filename}"
    return f"{CF_CDN_BASE}/{s[:-3]}/{int(s[-3:])}/{filename}"


async def fetch_filename(
    client: httpx.AsyncClient, project_id: int, file_id: int,
) -> str:
    """Hit the unofficial v1 endpoint to get this fileId's filename."""
    url = f"{CF_WEBSITE_API}/{project_id}/files/{file_id}"
    r = await client.get(url)
    if r.status_code == 404:
        raise CurseForgeError(
            f"CF doesn't know file {file_id} for project {project_id} "
            f"(404 from {url}) — manifest is out of date or the mod was deleted"
        )
    r.raise_for_status()
    payload = r.json().get("data") or {}
    name = payload.get("fileName")
    if not name:
        raise CurseForgeError(
            f"CF returned no fileName for {project_id}/{file_id}: {payload!r}"
        )
    return name


async def download_mod(
    client: httpx.AsyncClient, entry: CFEntry, mods_dir: Path,
) -> Path:
    """Resolve `entry` → filename via the v1 API, then fetch the jar
    from edge.forgecdn.net into `mods_dir`. Returns the on-disk path.

    Files where the mod author has set "third-party downloads disabled"
    surface as a 403 from the CDN. We catch that and raise a clean
    CurseForgeError that names the specific mod so the operator can
    fetch it manually.
    """
    filename = await fetch_filename(client, entry.project_id, entry.file_id)
    mods_dir.mkdir(parents=True, exist_ok=True)
    target = mods_dir / filename
    if target.exists() and target.stat().st_size > 0:
        return target  # cached; skip
    url = _cdn_url(entry.file_id, filename)
    try:
        async with client.stream("GET", url, follow_redirects=True) as resp:
            if resp.status_code == 403:
                raise CurseForgeError(
                    f"CF blocked download of {filename} "
                    f"(project {entry.project_id}, file {entry.file_id}). "
                    f"The mod author opted out of third-party launchers; "
                    f"download it manually from "
                    f"https://www.curseforge.com/projects/{entry.project_id} "
                    f"and drop it in {mods_dir}."
                )
            resp.raise_for_status()
            tmp = target.with_suffix(target.suffix + ".part")
            with tmp.open("wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=256 * 1024):
                    f.write(chunk)
            tmp.rename(target)
    except httpx.HTTPError as e:
        raise CurseForgeError(
            f"download failed for {filename}: {e}"
        ) from e
    return target


async def download_all_mods(
    client: httpx.AsyncClient,
    entries: list[CFEntry] | tuple[CFEntry, ...],
    mods_dir: Path,
    *,
    parallel: int = DEFAULT_PARALLEL,
    on_progress=None,
) -> tuple[list[Path], list[tuple[CFEntry, Exception]]]:
    """Fetch every entry in parallel (bounded). Returns
    (successes, failures). A single mod failing doesn't abort the run —
    we collect failures so the operator can see all of them at once.

    on_progress, if set, is called as `on_progress(done, total)` after
    each completion. Suitable for log output during a long install."""
    mods_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(parallel)
    total = len(entries)
    done = 0
    lock = asyncio.Lock()
    successes: list[Path] = []
    failures: list[tuple[CFEntry, Exception]] = []

    async def one(entry: CFEntry):
        nonlocal done
        async with sem:
            try:
                path = await download_mod(client, entry, mods_dir)
                successes.append(path)
            except CurseForgeError as e:
                failures.append((entry, e))
            async with lock:
                done += 1
                if on_progress is not None:
                    with contextlib.suppress(Exception):
                        on_progress(done, total)

    await asyncio.gather(*(one(e) for e in entries))
    return successes, failures
