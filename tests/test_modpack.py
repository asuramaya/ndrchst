"""Modpack platform tests — server-pack-from-URL install path."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import httpx
import pytest

from ndrchst.platforms.modpack import Modpack, ModpackInstallError
from ndrchst.runtime import jvm_installer as ji_mod


def _make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _handler(zip_bytes: bytes, *, return_html: bool = False):
    def h(request: httpx.Request) -> httpx.Response:
        if return_html:
            return httpx.Response(200, content=b"<html>captcha</html>")
        return httpx.Response(200, content=zip_bytes)
    return h


async def test_install_unzips_and_runs_neoforge_installer(tmp_path: Path, monkeypatch):
    """Server pack with installer.jar inside → unzip, run the installer,
    end up with run.sh."""
    zip_bytes = _make_zip({
        "neoforge-21.1.140-installer.jar": b"PK\x03\x04fake",
        "config/some-mod.toml": b"setting = true",
        "mods/example.jar": b"PK\x03\x04modjar",
    })
    captured: list[dict] = []

    def fake_run(*, workdir, args, image=None, **_):
        captured.append({"workdir": workdir, "args": args})
        # Simulate installer effects
        (workdir / "run.sh").write_text("#!/bin/bash\necho ok\n")
        (workdir / "user_jvm_args.txt").write_text("-Xmx4G\n")
        return ""

    monkeypatch.setattr("ndrchst.platforms.modpack.run_jdk_jar", fake_run)
    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler(zip_bytes)))
    mp = Modpack(client=client)
    art = await mp.install("https://example.com/server-pack.zip", tmp_path)
    await client.aclose()

    # Pack contents extracted
    assert (tmp_path / "mods" / "example.jar").exists()
    assert (tmp_path / "config" / "some-mod.toml").exists()
    # Installer was located and run
    assert len(captured) == 1
    assert captured[0]["args"][:2] == ["-jar", "neoforge-21.1.140-installer.jar"]
    # Memory args neutered
    assert "-Xmx" not in (tmp_path / "user_jvm_args.txt").read_text()
    # InstallArtifact points at run.sh
    assert art.entrypoint == "run.sh"
    # Temp zip cleaned up
    assert not (tmp_path / "_server-pack.zip").exists()


async def test_install_skips_installer_when_run_sh_already_present(
    tmp_path: Path, monkeypatch,
):
    """Some packs ship pre-baked — run.sh present, no installer needed."""
    zip_bytes = _make_zip({
        "run.sh": b"#!/bin/bash\njava nogui\n",
        "mods/x.jar": b"x",
        "user_jvm_args.txt": b"-Xmx8G\n",
    })
    called = []

    def fake_run(**kw):
        called.append(kw)
        return ""

    monkeypatch.setattr("ndrchst.platforms.modpack.run_jdk_jar", fake_run)
    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler(zip_bytes)))
    mp = Modpack(client=client)
    art = await mp.install("https://example.com/server-pack.zip", tmp_path)
    await client.aclose()

    assert called == []  # no installer ran
    assert art.entrypoint == "run.sh"
    assert (tmp_path / "run.sh").exists()


async def test_install_rejects_non_https_url(tmp_path: Path):
    mp = Modpack(client=httpx.AsyncClient())
    with pytest.raises(ModpackInstallError, match="https"):
        await mp.install("http://example.com/p.zip", tmp_path)
    with pytest.raises(ModpackInstallError, match="https"):
        await mp.install("file:///etc/passwd", tmp_path)


async def test_install_accepts_loopback_http(tmp_path: Path, monkeypatch):
    """http://127.0.0.1 is fine — used for the 'serve a local zip via
    a temporary http.server' pattern. http://example.com still rejected."""
    zip_bytes = _make_zip({
        "run.sh": b"#!/bin/bash\njava nogui\n",
    })

    def h(request: httpx.Request) -> httpx.Response:
        if "127.0.0.1" in str(request.url) or "localhost" in str(request.url):
            return httpx.Response(200, content=zip_bytes)
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(h))
    mp = Modpack(client=client)
    # 127.0.0.1 ok
    await mp.install("http://127.0.0.1:9999/p.zip", tmp_path / "a")
    # localhost ok
    await mp.install("http://localhost:9999/p.zip", tmp_path / "b")
    # Other http still rejected
    with pytest.raises(ModpackInstallError, match="https"):
        await mp.install("http://evil.example/p.zip", tmp_path / "c")
    await client.aclose()


async def test_install_rejects_non_zip_response(tmp_path: Path):
    """CurseForge sometimes returns an HTML interstitial instead of the file."""
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(_handler(b"", return_html=True))
    )
    mp = Modpack(client=client)
    with pytest.raises(ModpackInstallError, match="not a zip"):
        await mp.install("https://example.com/p.zip", tmp_path)
    await client.aclose()


async def test_install_rejects_zip_with_no_installer_and_no_run_sh(
    tmp_path: Path, monkeypatch,
):
    """A pack that ships just config + mods (no installer, no run.sh) is
    unusable — surface a clear error rather than silently failing."""
    zip_bytes = _make_zip({
        "mods/x.jar": b"x",
        "config/y.toml": b"y",
    })
    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler(zip_bytes)))
    mp = Modpack(client=client)
    with pytest.raises(ModpackInstallError, match=r"no run\.sh and no NeoForge installer"):
        await mp.install("https://example.com/p.zip", tmp_path)
    await client.aclose()


async def test_install_rejects_zip_slip(tmp_path: Path):
    """A zip with `../escape` paths must be refused."""
    zip_bytes = _make_zip({
        "../escape.txt": b"oops",
        "neoforge-21.1.140-installer.jar": b"x",
    })
    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler(zip_bytes)))
    mp = Modpack(client=client)
    with pytest.raises(ModpackInstallError, match="escape data dir"):
        await mp.install("https://example.com/p.zip", tmp_path)
    await client.aclose()


async def test_install_propagates_installer_errors(tmp_path: Path, monkeypatch):
    zip_bytes = _make_zip({
        "neoforge-21.1.140-installer.jar": b"x",
    })

    def fake_run(**_):
        raise ji_mod.JvmInstallError("crashed")

    monkeypatch.setattr("ndrchst.platforms.modpack.run_jdk_jar", fake_run)
    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler(zip_bytes)))
    mp = Modpack(client=client)
    with pytest.raises(ModpackInstallError, match="installer failed"):
        await mp.install("https://example.com/p.zip", tmp_path)
    await client.aclose()


async def test_versions_returns_empty():
    """Modpack has no canonical version listing — versions() is just an
    empty list. Lifecycle's `version='latest'` resolution surfaces this
    as a friendly 'no versions available' error."""
    mp = Modpack(client=httpx.AsyncClient())
    assert await mp.versions() == []


# ─── CF client-pack branch ─────────────────────────────────────────────────


def _cf_client_zip(*, files, overrides=None) -> bytes:
    import json as _json
    manifest = {
        "minecraft": {
            "version": "1.21.1",
            "modLoaders": [{"id": "neoforge-21.1.228", "primary": True}],
        },
        "name": "ATM-like", "version": "1.0", "overrides": "overrides",
        "files": files,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", _json.dumps(manifest))
        for path, content in (overrides or {}).items():
            zf.writestr(f"overrides/{path}", content)
    return buf.getvalue()


async def test_install_cf_client_pack_full_flow(tmp_path: Path, monkeypatch):
    """A CF client-pack zip should:
      1. download a NeoForge installer that matches the manifest's loader
      2. run it (we mock the JDK runner)
      3. resolve every mod via the v1 API + CDN
      4. drop overrides over the data dir
    """
    pack_zip = _cf_client_zip(
        files=[
            {"required": True, "projectID": 100, "fileID": 7471280},
            {"required": True, "projectID": 200, "fileID": 7574260},
        ],
        overrides={
            "config/test.toml": b"setting = 1",
            "kubejs/server_scripts/x.js": b"// hi",
        },
    )

    installer_ran: list[Path] = []

    def fake_run(*, workdir, args, image=None, **_):
        installer_ran.append(workdir)
        # Pretend NeoForge installer succeeded
        (workdir / "run.sh").write_text("#!/bin/bash\njava nogui\n")
        (workdir / "user_jvm_args.txt").write_text("-Xmx2G\n-XX:+UseG1GC\n")
        return ""

    monkeypatch.setattr("ndrchst.platforms.modpack.run_jdk_jar", fake_run)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        # The pack zip itself (served from "evil:// fictional URL")
        if url.endswith("/pack.zip"):
            return httpx.Response(200, content=pack_zip)
        # NeoForge installer download
        if "maven.neoforged.net" in url:
            return httpx.Response(200, content=b"PK\x03\x04fake-installer")
        # CF v1 API: file metadata
        if "/api/v1/mods/" in url:
            parts = url.rstrip("/").split("/")
            fid = int(parts[-1])
            pid = int(parts[-3])
            name = {(100, 7471280): "modA.jar", (200, 7574260): "modB.jar"}.get(
                (pid, fid)
            )
            if name is None:
                return httpx.Response(404)
            return httpx.Response(200, json={
                "data": {"id": fid, "projectId": pid, "fileName": name},
            })
        # CDN
        if "forgecdn.net" in url:
            if "modA.jar" in url:
                return httpx.Response(200, content=b"PK\x03\x04modA-content")
            if "modB.jar" in url:
                return httpx.Response(200, content=b"PK\x03\x04modB-content")
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    mp = Modpack(client=client)
    art = await mp.install("https://example.com/pack.zip", tmp_path / "data")
    await client.aclose()

    # NeoForge installer ran in the data dir
    assert len(installer_ran) == 1
    assert installer_ran[0] == tmp_path / "data"
    # Mods landed in mods/
    mods = tmp_path / "data" / "mods"
    assert (mods / "modA.jar").read_bytes() == b"PK\x03\x04modA-content"
    assert (mods / "modB.jar").read_bytes() == b"PK\x03\x04modB-content"
    # Overrides applied
    assert (tmp_path / "data" / "config" / "test.toml").read_text() == "setting = 1"
    assert (tmp_path / "data" / "kubejs" / "server_scripts" / "x.js").exists()
    # user_jvm_args.txt: -Xmx stripped, GC flags kept, comment added
    body = (tmp_path / "data" / "user_jvm_args.txt").read_text()
    assert "-Xmx" not in body
    assert "-XX:+UseG1GC" in body
    assert "ndrchst" in body
    # Pack zip itself cleaned up
    assert not (tmp_path / "data" / "_server-pack.zip").exists()
    # InstallArtifact points at run.sh
    assert art.entrypoint == "run.sh"


async def test_install_cf_client_pack_continues_on_partial_failures(
    tmp_path: Path, monkeypatch,
):
    """Manifest rot is the operator's problem, not the install's. The
    install completes with a missing-mods sidecar; the operator drops
    the jars manually and the server-driven sync propagates them to
    every pilot. No hard abort."""
    entries = [
        {"required": True, "projectID": 100 + i, "fileID": 7000000 + i}
        for i in range(10)
    ]
    pack_zip = _cf_client_zip(files=entries)

    def fake_run(**_):
        (tmp_path / "data").mkdir(exist_ok=True)
        (tmp_path / "data" / "run.sh").write_text("#!/bin/bash\n")
        return ""

    monkeypatch.setattr("ndrchst.platforms.modpack.run_jdk_jar", fake_run)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/pack.zip"):
            return httpx.Response(200, content=pack_zip)
        if "maven.neoforged.net" in url:
            return httpx.Response(200, content=b"x")
        if "/api/v1/mods/" in url:
            parts = url.rstrip("/").split("/")
            fid = int(parts[-1])
            pid = int(parts[-3])
            # Two of ten files vanished from CF
            if fid in (7000000, 7000001):
                return httpx.Response(200, json={"data": None})
            return httpx.Response(200, json={
                "data": {"id": fid, "projectId": pid, "fileName": f"mod-{fid}.jar"},
            })
        if "forgecdn.net" in url:
            return httpx.Response(200, content=b"x")
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    mp = Modpack(client=client)
    art = await mp.install("https://example.com/pack.zip", tmp_path / "data")
    await client.aclose()
    missing = (tmp_path / "data" / "ndrchst-missing-mods.txt").read_text()
    assert "7000000" in missing
    assert "7000001" in missing
    assert len(list((tmp_path / "data" / "mods").iterdir())) == 8
    assert art.entrypoint == "run.sh"
