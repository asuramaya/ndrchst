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
