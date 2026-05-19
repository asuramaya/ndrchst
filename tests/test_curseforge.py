"""CurseForge manifest-resolver tests."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from ndrchst.runtime import curseforge as cf


def _client_pack_zip(*, files=None, overrides=None) -> bytes:
    """Build a minimal CF client-pack zip in memory."""
    files = files if files is not None else [
        {"required": True, "projectID": 100, "fileID": 7471280},
        {"required": True, "projectID": 200, "fileID": 1234567},
    ]
    manifest = {
        "minecraft": {
            "version": "1.21.1",
            "modLoaders": [{"id": "neoforge-21.1.228", "primary": True}],
        },
        "name": "Test Pack",
        "version": "1.0",
        "overrides": "overrides",
        "files": files,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for path, content in (overrides or {}).items():
            zf.writestr(f"overrides/{path}", content)
    return buf.getvalue()


# ─── read_manifest ──────────────────────────────────────────────────────────


def test_read_manifest_parses_client_pack(tmp_path: Path):
    z = tmp_path / "p.zip"
    z.write_bytes(_client_pack_zip())
    m = cf.read_manifest(z)
    assert m.name == "Test Pack"
    assert m.version == "1.0"
    assert m.mc_version == "1.21.1"
    assert m.loader_id == "neoforge-21.1.228"
    assert m.loader_version == "21.1.228"
    assert len(m.files) == 2
    assert m.files[0].project_id == 100
    assert m.files[0].file_id == 7471280
    assert m.overrides_dir == "overrides"


def test_read_manifest_rejects_server_pack(tmp_path: Path):
    """A server pack has no manifest.json at the root — surface that
    distinction with a clear error rather than crashing on JSON parse."""
    z = tmp_path / "p.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("run.sh", "#!/bin/bash\n")
        zf.writestr("mods/x.jar", "x")
    z.write_bytes(buf.getvalue())
    with pytest.raises(cf.CurseForgeError, match=r"no manifest\.json"):
        cf.read_manifest(z)


def test_read_manifest_rejects_non_neoforge_packs(tmp_path: Path):
    """We only support NeoForge right now. A Forge or Fabric pack should
    surface a friendly error, not a cryptic install failure later."""
    bad = {
        "minecraft": {
            "version": "1.21.1",
            "modLoaders": [{"id": "forge-52.0.0", "primary": True}],
        },
        "name": "x", "version": "1", "files": [],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(bad))
    z = tmp_path / "p.zip"
    z.write_bytes(buf.getvalue())
    with pytest.raises(cf.CurseForgeError, match="only supports NeoForge"):
        cf.read_manifest(z)


def test_read_manifest_handles_bad_json(tmp_path: Path):
    z = tmp_path / "p.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", "this is { not json")
    z.write_bytes(buf.getvalue())
    with pytest.raises(cf.CurseForgeError, match="valid JSON"):
        cf.read_manifest(z)


# ─── apply_overrides ────────────────────────────────────────────────────────


def test_apply_overrides_extracts_overrides_only(tmp_path: Path):
    z = tmp_path / "p.zip"
    z.write_bytes(_client_pack_zip(overrides={
        "config/foo.toml": b"key = value",
        "kubejs/server_scripts/x.js": b"console.log('x')",
    }))
    n = cf.apply_overrides(z, tmp_path / "data", "overrides")
    assert n == 2
    assert (tmp_path / "data" / "config" / "foo.toml").read_text() == "key = value"
    assert (tmp_path / "data" / "kubejs" / "server_scripts" / "x.js").exists()
    # The manifest itself isn't extracted — it lives outside overrides/
    assert not (tmp_path / "data" / "manifest.json").exists()


def test_apply_overrides_rejects_zip_slip(tmp_path: Path):
    z = tmp_path / "p.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", "{}")
        zf.writestr("overrides/../escape.txt", b"oops")
    z.write_bytes(buf.getvalue())
    with pytest.raises(cf.CurseForgeError, match="escape data dir"):
        cf.apply_overrides(z, tmp_path / "data", "overrides")


# ─── CDN URL ────────────────────────────────────────────────────────────────


def test_cdn_url_splits_modern_file_id():
    # 7471280 → 7471/280/
    url = cf._cdn_url(7471280, "kotlinforforge-5.11.0-all.jar")
    assert url == (
        "https://edge.forgecdn.net/files/7471/280/kotlinforforge-5.11.0-all.jar"
    )


def test_cdn_url_keeps_leading_zeros_correctly():
    # 7574260 → 7574/260/
    url = cf._cdn_url(7574260, "ComplementaryUnbound_r5.7.1.zip")
    assert "/7574/260/" in url


def test_cdn_url_handles_short_legacy_id():
    # 1234 (4-digit) → /0/1234/ — legacy path, mostly historical interest
    url = cf._cdn_url(1234, "foo.jar")
    assert url.endswith("/0/1234/foo.jar")


# ─── fetch_filename + download_mod ──────────────────────────────────────────


def _cf_handler(*, names: dict[tuple[int, int], str], jars: dict[int, bytes],
                blocked: set[int] | None = None):
    """Mock for CF v1 endpoint + edge.forgecdn.net CDN.
    `names` maps (project_id, file_id) → filename.
    `jars` maps file_id → bytes the CDN should serve.
    `blocked` is a set of file_ids the CDN should 403 on.
    """
    blocked = blocked or set()

    def h(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        # API: /api/v1/mods/<pid>/files/<fid>
        if "/api/v1/mods/" in url:
            parts = url.rstrip("/").split("/")
            fid = int(parts[-1])
            pid = int(parts[-3])
            name = names.get((pid, fid))
            if name is None:
                return httpx.Response(404)
            return httpx.Response(200, json={"data": {
                "id": fid, "projectId": pid, "fileName": name,
            }})
        # CDN: /files/<id1>/<id2>/<filename>
        if "edge.forgecdn.net" in url or "mediafilez.forgecdn.net" in url:
            parts = url.rstrip("/").split("/")
            # Recover the file_id from the path: ...<id1>/<id2>/<file>
            id1 = parts[-3]
            id2 = parts[-2]
            fid = int(id1) * 1000 + int(id2)
            if fid in blocked:
                return httpx.Response(403, content=b"third-party blocked")
            data = jars.get(fid)
            if data is None:
                return httpx.Response(404)
            return httpx.Response(200, content=data)
        return httpx.Response(404)
    return h


async def test_fetch_filename_returns_metadata():
    h = _cf_handler(names={(100, 7471280): "kotlinforforge-5.11.0-all.jar"},
                    jars={})
    client = httpx.AsyncClient(transport=httpx.MockTransport(h))
    name = await cf.fetch_filename(client, 100, 7471280)
    assert name == "kotlinforforge-5.11.0-all.jar"
    await client.aclose()


async def test_fetch_filename_404_raises():
    h = _cf_handler(names={}, jars={})
    client = httpx.AsyncClient(transport=httpx.MockTransport(h))
    with pytest.raises(cf.CurseForgeError, match="404"):
        await cf.fetch_filename(client, 999, 999)
    await client.aclose()


async def test_download_mod_writes_jar(tmp_path: Path):
    h = _cf_handler(
        names={(100, 7471280): "kotlinforforge-5.11.0-all.jar"},
        jars={7471280: b"PK\x03\x04fake-jar-bytes"},
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(h))
    e = cf.CFEntry(project_id=100, file_id=7471280, required=True)
    p = await cf.download_mod(client, e, tmp_path / "mods")
    assert p.name == "kotlinforforge-5.11.0-all.jar"
    assert p.read_bytes() == b"PK\x03\x04fake-jar-bytes"
    # .part temp cleaned up
    assert not p.with_suffix(p.suffix + ".part").exists()
    await client.aclose()


async def test_download_mod_403_surfaces_third_party_blocked(tmp_path: Path):
    h = _cf_handler(
        names={(100, 7471280): "blocked-mod.jar"},
        jars={7471280: b"x"},
        blocked={7471280},
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(h))
    e = cf.CFEntry(project_id=100, file_id=7471280, required=True)
    with pytest.raises(cf.CurseForgeError, match="third-party launchers"):
        await cf.download_mod(client, e, tmp_path / "mods")
    await client.aclose()


async def test_download_mod_skips_cached(tmp_path: Path):
    """If the jar already exists on disk we don't re-download — important
    for idempotent install + iterative dev."""
    h = _cf_handler(
        names={(100, 7471280): "cached.jar"},
        jars={},  # CDN would 404 if asked, ensuring we don't call it
    )
    mods = tmp_path / "mods"
    mods.mkdir()
    (mods / "cached.jar").write_bytes(b"already-here")
    client = httpx.AsyncClient(transport=httpx.MockTransport(h))
    e = cf.CFEntry(project_id=100, file_id=7471280, required=True)
    p = await cf.download_mod(client, e, mods)
    assert p.read_bytes() == b"already-here"
    await client.aclose()


# ─── download_all_mods ──────────────────────────────────────────────────────


async def test_download_all_mods_parallel_success(tmp_path: Path):
    entries = [
        cf.CFEntry(project_id=100, file_id=1471280, required=True),
        cf.CFEntry(project_id=200, file_id=2471280, required=True),
        cf.CFEntry(project_id=300, file_id=3471280, required=True),
    ]
    h = _cf_handler(
        names={(e.project_id, e.file_id): f"mod-{e.project_id}.jar" for e in entries},
        jars={e.file_id: f"jar-{e.file_id}".encode() for e in entries},
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(h))
    progress_calls: list[tuple[int, int]] = []
    successes, failures = await cf.download_all_mods(
        client, entries, tmp_path / "mods",
        parallel=4, on_progress=lambda d, t: progress_calls.append((d, t)),
    )
    assert len(successes) == 3
    assert len(failures) == 0
    # Progress fired for every completion
    assert progress_calls[-1] == (3, 3)
    await client.aclose()


# ─── URL parsing + server-pack resolution ───────────────────────────────────


def test_file_id_from_cdn_url():
    assert cf.file_id_from_url(
        "https://edge.forgecdn.net/files/8091/114/All-the-Mods-10-7.0.zip"
    ) == 8091114
    # mediafilez.forgecdn.net (where edge redirects) is also valid
    assert cf.file_id_from_url(
        "https://mediafilez.forgecdn.net/files/7471/280/kotlin.jar"
    ) == 7471280
    # leading-zero remainder needs to round-trip: 8094/893 → 8094893
    assert cf.file_id_from_url(
        "https://edge.forgecdn.net/files/8094/893/ServerFiles-7.0.zip"
    ) == 8094893


def test_file_id_from_page_url():
    assert cf.file_id_from_url(
        "https://www.curseforge.com/minecraft/modpacks/all-the-mods-10/files/8091114"
    ) == 8091114
    # also works without www.
    assert cf.file_id_from_url(
        "https://curseforge.com/minecraft/modpacks/atm10/files/8091114/download"
    ) == 8091114


def test_file_id_from_url_returns_none_for_garbage():
    assert cf.file_id_from_url("https://example.com/foo.zip") is None
    assert cf.file_id_from_url("not even a url") is None
    assert cf.file_id_from_url("") is None


def _resolver_handler(*, project_id, file_id, server_pack_id=None, server_pack_filename=None):
    """Mock the unofficial CF v1 endpoints used by the resolver."""
    def h(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        # /api/v1/mods/files/<fid> — reverse-lookup file → project
        if url.endswith(f"/api/v1/mods/files/{file_id}"):
            return httpx.Response(200, json={
                "data": {"id": file_id, "modId": project_id, "projectId": project_id},
            })
        # /api/v1/mods/<pid>/files/<fid> — file metadata
        if url.endswith(f"/api/v1/mods/{project_id}/files/{file_id}"):
            return httpx.Response(200, json={
                "data": {
                    "id": file_id, "projectId": project_id,
                    "fileName": "client-pack.zip",
                    "hasServerPack": server_pack_id is not None,
                    "additionalFilesCount": 1 if server_pack_id else 0,
                },
            })
        # /api/v1/mods/<pid>/files/<fid>/additional-files — server pack zip
        if url.endswith(f"/files/{file_id}/additional-files"):
            data = []
            if server_pack_id:
                data.append({"id": server_pack_id, "fileName": server_pack_filename})
            return httpx.Response(200, json={"data": data})
        return httpx.Response(404)
    return h


async def test_resolve_to_server_pack_upgrades_client_pack_url():
    """User pastes the CDN URL of the client pack; we upgrade to the
    server-pack CDN URL because hasServerPack=True."""
    h = _resolver_handler(
        project_id=925200, file_id=8091114,
        server_pack_id=8094893, server_pack_filename="ServerFiles-7.0.zip",
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(h))
    new_url, note = await cf.resolve_to_server_pack(
        client, "https://edge.forgecdn.net/files/8091/114/Client.zip",
    )
    assert new_url == (
        "https://edge.forgecdn.net/files/8094/893/ServerFiles-7.0.zip"
    )
    assert note and "server pack" in note.lower()
    await client.aclose()


async def test_resolve_to_server_pack_passthrough_when_no_server_pack():
    """If hasServerPack=False, we return the original URL unchanged."""
    h = _resolver_handler(project_id=100, file_id=999)
    client = httpx.AsyncClient(transport=httpx.MockTransport(h))
    original = "https://edge.forgecdn.net/files/0/999/foo.zip"
    new_url, note = await cf.resolve_to_server_pack(client, original)
    assert new_url == original
    assert note is None
    await client.aclose()


async def test_resolve_to_server_pack_passthrough_for_non_cf_url():
    """Not a CF URL at all → no lookup attempted, original URL returned."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    original = "https://example.com/some-other-modpack.zip"
    new_url, note = await cf.resolve_to_server_pack(client, original)
    assert new_url == original
    assert note is None
    await client.aclose()


async def test_resolve_to_server_pack_swallows_lookup_failures():
    """If CF returns garbage / 5xx for the lookup, we fall back to the
    original URL rather than failing the whole install. The operator
    still gets a usable (if non-curated) zip."""
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500)),
    )
    original = "https://edge.forgecdn.net/files/8091/114/Client.zip"
    new_url, note = await cf.resolve_to_server_pack(client, original)
    assert new_url == original
    assert note is None
    await client.aclose()


async def test_download_all_mods_partial_failure(tmp_path: Path):
    """One mod 403s, others succeed — the run completes with a failure list,
    not an exception. The Modpack platform decides what to do with that."""
    entries = [
        cf.CFEntry(project_id=100, file_id=1471280, required=True),
        cf.CFEntry(project_id=200, file_id=2471280, required=True),  # blocked
    ]
    h = _cf_handler(
        names={(e.project_id, e.file_id): f"mod-{e.project_id}.jar" for e in entries},
        jars={1471280: b"ok"},
        blocked={2471280},
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(h))
    successes, failures = await cf.download_all_mods(
        client, entries, tmp_path / "mods", parallel=2,
    )
    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0][0].file_id == 2471280
    await client.aclose()
