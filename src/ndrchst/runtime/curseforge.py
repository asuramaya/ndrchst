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
import re
import shutil
import urllib.parse
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger("ndrchst.curseforge")

CF_WEBSITE_API = "https://www.curseforge.com/api/v1/mods"
CF_CDN_BASE = "https://edge.forgecdn.net/files"
# Public cfwidget mirror, keyed by slug. The CF v1 endpoints all require a
# numeric projectId; cfwidget is the one no-auth way we have to resolve
# a slug we lifted from a URL to that projectId.
CFWIDGET_API = "https://api.cfwidget.com"

# Patterns we know how to extract a CurseForge fileId from.
_CDN_RE = re.compile(
    r"^https?://(?:edge|mediafilez?)\.forgecdn\.net/files/(\d+)/(\d+)/"
)
# Page URL like:
#   https://www.curseforge.com/minecraft/modpacks/all-the-mods-10/files/8091114
# We need both the category/slug pair (to find the project) and the fileId.
_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?curseforge\.com/([^/]+)/([^/]+)/([^/]+)/files/(\d+)"
)

# Default concurrency for the parallel mod-download phase. A typical
# kitchen-sink modpack has 400-600 mods at a few hundred KB to a few MB
# each. 16 is enough to saturate a residential link without melting CF's
# CDN or hitting their rate limit (anecdotal: hundreds of files/min ok).
DEFAULT_PARALLEL = 16

# A manifest's files[] mixes mod jars with non-jar assets — shaderpacks,
# resourcepacks — that NeoForge loads from their own directories, NOT
# mods/. Dropping a shader .zip in mods/ does nothing (best case) and
# trips EuphoriaPatcher (worst: it can't find the base shader to patch).
# We classify each non-jar by its CurseForge project type (via cfwidget,
# which is the one no-auth way to read a project's type) and route it to
# the directory the client actually loads it from.
_CF_TYPE_TO_DIR = {
    "shaders": "shaderpacks",
    "resource packs": "resourcepacks",
    "texture packs": "resourcepacks",
    "worlds": "saves",
}


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
    try:
        r = await client.get(url)
    except httpx.HTTPError as e:
        raise CurseForgeError(
            f"CF metadata request failed for {project_id}/{file_id}: {e}"
        ) from e
    if r.status_code == 404:
        raise CurseForgeError(
            f"CF doesn't know file {file_id} for project {project_id} "
            f"(404 from {url}) — manifest is out of date or the mod was deleted"
        )
    if r.status_code == 403:
        raise CurseForgeError(
            f"CF returned 403 for {project_id}/{file_id} — the project is "
            f"unlisted or the endpoint is blocked for this client"
        )
    if r.status_code >= 400:
        raise CurseForgeError(
            f"CF returned HTTP {r.status_code} for {project_id}/{file_id}"
        )
    payload = r.json().get("data") or {}
    name = payload.get("fileName")
    if not name:
        raise CurseForgeError(
            f"CF returned no fileName for {project_id}/{file_id} "
            f"(file was likely deleted from the mod's project page; "
            f"the modpack manifest is out of date)"
        )
    return name


async def pack_cdn_url(
    client: httpx.AsyncClient, project_id: int, file_id: int,
) -> str:
    """Public CF CDN URL for a *pack* file (same `_cdn_url` construction as
    mod jars — it bypasses CF's download-disabled flag). The filename is
    fetched live so the URL tracks the pack: pin the fileId, re-resolve to
    stay synced when the pack updates. Spaces are percent-encoded so the
    result is fetch-ready (pack filenames like "All the Mods 10-7.0.zip"
    have spaces; mod jars don't, which is why `_cdn_url` doesn't quote)."""
    filename = await fetch_filename(client, project_id, file_id)
    return _cdn_url(file_id, urllib.parse.quote(filename))


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


@dataclass(frozen=True, slots=True)
class CFURLParts:
    """What we can extract from a CurseForge URL without hitting the network.

    `slug_path` is the `<category>/<type>/<slug>` triple from page URLs
    (e.g. `minecraft/modpacks/all-the-mods-10`) — we feed it to cfwidget
    to resolve the projectId. CDN URLs don't carry the slug so this
    field is None for them.
    """
    file_id: int
    slug_path: str | None


def parse_cf_url(url: str) -> CFURLParts | None:
    """Best-effort parse of a CurseForge URL. Returns None if the URL
    isn't a CF link we recognise."""
    m = _CDN_RE.match(url)
    if m:
        return CFURLParts(
            file_id=int(m.group(1) + f"{int(m.group(2)):03d}"),
            slug_path=None,
        )
    m = _PAGE_RE.match(url)
    if m:
        return CFURLParts(
            file_id=int(m.group(4)),
            slug_path=f"{m.group(1)}/{m.group(2)}/{m.group(3)}",
        )
    return None


def file_id_from_url(url: str) -> int | None:
    """Convenience: just the fileId if there is one."""
    parts = parse_cf_url(url)
    return parts.file_id if parts else None


async def project_id_from_slug(
    client: httpx.AsyncClient, slug_path: str,
) -> int:
    """Resolve a slug like `minecraft/modpacks/all-the-mods-10` to its
    integer projectId via cfwidget. Used by the resolver since the
    no-auth CF v1 endpoints all require a known projectId."""
    r = await client.get(f"{CFWIDGET_API}/{slug_path}")
    if r.status_code == 404:
        raise CurseForgeError(f"cfwidget doesn't know slug {slug_path!r}")
    r.raise_for_status()
    payload = r.json()
    pid = payload.get("id")
    if not pid:
        raise CurseForgeError(
            f"cfwidget returned no project id for {slug_path!r}: {payload!r}"
        )
    return int(pid)


async def fetch_file_metadata(
    client: httpx.AsyncClient, project_id: int, file_id: int,
) -> dict:
    """Like fetch_filename but returns the full ``data`` payload. Raises
    CurseForgeError on 404 / null / blocked."""
    url = f"{CF_WEBSITE_API}/{project_id}/files/{file_id}"
    r = await client.get(url)
    if r.status_code == 404:
        raise CurseForgeError(f"CF 404 for {project_id}/{file_id}")
    r.raise_for_status()
    payload = r.json().get("data")
    if not payload:
        raise CurseForgeError(f"CF returned null data for {project_id}/{file_id}")
    return payload


async def find_server_pack_url(
    client: httpx.AsyncClient, project_id: int, file_id: int,
) -> str | None:
    """If the given CF file has a published server-pack companion,
    return the CDN URL for that pack. Returns None if no server pack
    exists, or if the lookup fails for any reason (we never raise —
    callers fall back to using the original URL).

    Modpacks on CurseForge often publish two zips per release: the
    "main" file (client pack with manifest.json) plus an additional
    "ServerFiles-X.Y.zip" linked via the ``additionalFilesCount`` /
    ``hasServerPack`` flags. The additional-files endpoint returns the
    server-pack file record.
    """
    try:
        meta = await fetch_file_metadata(client, project_id, file_id)
        if not meta.get("hasServerPack"):
            return None
        url = f"{CF_WEBSITE_API}/{project_id}/files/{file_id}/additional-files"
        r = await client.get(url)
        r.raise_for_status()
        extras = r.json().get("data") or []
        if not extras:
            return None
        srv = extras[0]
        srv_fid = int(srv["id"])
        srv_filename = srv["fileName"]
        return _cdn_url(srv_fid, srv_filename)
    except (CurseForgeError, httpx.HTTPError, KeyError, ValueError) as e:
        log.warning(
            "server-pack lookup failed for project %s file %s: %s",
            project_id, file_id, e,
        )
        return None


async def resolve_to_server_pack(
    client: httpx.AsyncClient, url: str,
) -> tuple[str, str | None]:
    """Best-effort upgrade of a CurseForge URL to its server-pack zip.

    Returns ``(resolved_url, note)`` where ``note`` is a short human
    message describing what happened (or None if no swap). The caller
    uses ``resolved_url`` to actually download; ``note`` is surfaced to
    the operator so they understand the swap.

    Non-CF URLs pass through unchanged. CDN-only URLs (no slug → no way
    to find the projectId without auth) also pass through. Page URLs
    like ``curseforge.com/minecraft/modpacks/<slug>/files/<fileId>``
    get the full server-pack-swap treatment.
    """
    parts = parse_cf_url(url)
    if parts is None:
        return url, None
    if parts.slug_path is None:
        # CDN URL with no slug — we can't find the projectId without
        # auth, so pass through. Operator can paste the page URL instead
        # if they want the auto-swap.
        return url, None
    try:
        project_id = await project_id_from_slug(client, parts.slug_path)
    except (CurseForgeError, httpx.HTTPError) as e:
        log.warning("project lookup failed for %s: %s", parts.slug_path, e)
        return url, None
    server_pack_url = await find_server_pack_url(client, project_id, parts.file_id)
    if server_pack_url is None:
        return url, None
    return server_pack_url, (
        f"auto-upgraded to CurseForge server pack "
        f"(project {project_id}, file {parts.file_id})"
    )


async def _classify_nonjar(client: httpx.AsyncClient, project_id: int) -> str | None:
    """Return the client subdir a non-jar CF asset loads from
    (shaderpacks / resourcepacks / saves), or None if we can't map its
    type. cfwidget keyed by project id is the no-auth source of a
    project's type; CF's own v1 project endpoint 403s for unauthed
    clients."""
    try:
        r = await client.get(f"{CFWIDGET_API}/{project_id}")
        r.raise_for_status()
        ptype = (r.json().get("type") or "").strip().lower()
    except (httpx.HTTPError, ValueError, KeyError):
        return None
    return _CF_TYPE_TO_DIR.get(ptype)


async def resolve_manifest_targets(
    client: httpx.AsyncClient,
    entries: list[CFEntry] | tuple[CFEntry, ...],
    *,
    parallel: int = DEFAULT_PARALLEL,
    on_progress=None,
) -> dict[str, dict]:
    """Like resolve_manifest_urls but also classifies WHERE each file
    belongs on the client. Returns ``{filename: {"url", "target"}}``:

      - ``.jar`` → ``target="mods"``
      - shader/resource/world packs → their matching subdir
        (shaderpacks, resourcepacks, saves)

    Non-jar files whose CF project type we can't classify are omitted —
    we'd rather not litter the install with a file we can't place. Dead
    files (CF 404) are skipped the same way resolve_manifest_urls does.
    """
    sem = asyncio.Semaphore(parallel)
    total = len(entries)
    done = 0
    lock = asyncio.Lock()
    out: dict[str, dict] = {}

    async def one(entry: CFEntry):
        nonlocal done
        async with sem:
            try:
                filename = await fetch_filename(client, entry.project_id, entry.file_id)
                if filename.endswith(".jar"):
                    out[filename] = {
                        "url": _cdn_url(entry.file_id, filename),
                        "target": "mods",
                    }
                else:
                    target = await _classify_nonjar(client, entry.project_id)
                    if target is not None:
                        out[filename] = {
                            "url": _cdn_url(entry.file_id, filename),
                            "target": target,
                        }
            except CurseForgeError:
                pass  # dead file → served from origin (or dropped)
            async with lock:
                done += 1
                if on_progress is not None:
                    with contextlib.suppress(Exception):
                        on_progress(done, total)

    await asyncio.gather(*(one(e) for e in entries))
    return out


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
