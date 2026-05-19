"""NeoForge platform unit tests using httpx MockTransport + a fake JDK runner.

Stresses:
  - versions() filters beta builds and sorts newest-first
  - install() downloads the installer jar then invokes run_jdk_jar
  - install() rewrites user_jvm_args.txt so it doesn't clobber lifecycle's -Xmx
  - install() raises if run.sh doesn't materialise (treated as installer failure)
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from ndrchst.platforms.neoforge import (
    NEOFORGE_MAVEN_LISTING,
    NeoForge,
)
from ndrchst.runtime import jvm_installer as ji_mod

_FAKE_INSTALLER_JAR = b"PK\x03\x04" + b"fake-neoforge-installer-jar" * 16


def _maven_listing_response() -> httpx.Response:
    """A mix of stable + beta releases at MC 1.21.4 / 1.21.11 / 1.26.1."""
    return httpx.Response(200, json={
        "isSnapshot": False,
        "versions": [
            "21.1.140",
            "21.4.100",
            "21.4.156",
            "21.4.157",         # latest 21.4 stable
            "21.11.41",
            "21.11.42",         # latest 21.11 stable
            "26.1.0.0-alpha.9", # filtered
            "21.10.64-beta",    # filtered
        ],
    })


def _make_handler():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == NEOFORGE_MAVEN_LISTING:
            return _maven_listing_response()
        # Installer download — match the URL template.
        if url.startswith("https://maven.neoforged.net/releases/net/neoforged/neoforge/"):
            return httpx.Response(200, content=_FAKE_INSTALLER_JAR)
        return httpx.Response(404)
    return handler


async def test_versions_filters_betas_and_sorts_newest_first():
    client = httpx.AsyncClient(transport=httpx.MockTransport(_make_handler()))
    nf = NeoForge(client=client)
    versions = await nf.versions()
    names = [v.version for v in versions]
    # Newest stable first; no betas or alphas
    assert names[0] == "21.11.42"
    assert "21.11.41" in names
    assert "21.4.157" in names
    # Betas and alphas filtered
    assert all("-beta" not in n and "-alpha" not in n for n in names)
    await client.aclose()


async def test_install_downloads_installer_and_runs_it(tmp_path: Path, monkeypatch):
    """Happy path: installer is fetched, run_jdk_jar is invoked with the
    right args, and after the install run.sh exists so we succeed."""
    captured: list[dict] = []

    def fake_run(*, workdir, args, image=None, **_):
        captured.append({"image": image, "workdir": workdir, "args": args})
        # Simulate the installer's effects: drop run.sh, user_jvm_args.txt.
        (workdir / "run.sh").write_text("#!/bin/bash\njava @user_jvm_args.txt nogui\n")
        (workdir / "user_jvm_args.txt").write_text("-Xmx2G  # installer default\n")
        return "Installer finished."

    monkeypatch.setattr("ndrchst.platforms.neoforge.run_jdk_jar", fake_run)

    client = httpx.AsyncClient(transport=httpx.MockTransport(_make_handler()))
    nf = NeoForge(client=client)
    art = await nf.install("21.11.42", tmp_path / "srv")
    await client.aclose()

    # Installer jar was downloaded
    installer = tmp_path / "srv" / "neoforge-21.11.42-installer.jar"
    assert installer.exists()
    assert installer.read_bytes() == _FAKE_INSTALLER_JAR

    # The JDK runner got the right args
    assert len(captured) == 1
    assert captured[0]["args"] == ["-jar", installer.name, "--installServer", "/work"]
    assert captured[0]["workdir"] == tmp_path / "srv"

    # user_jvm_args.txt was rewritten so lifecycle's -Xmx isn't shadowed:
    # the only mention of Xmx remaining is in our explanatory comment, not
    # an actual `-Xmx2G` directive.
    user_jvm = (tmp_path / "srv" / "user_jvm_args.txt").read_text()
    assert "-Xmx2G" not in user_jvm
    assert "2G" not in user_jvm
    assert "ndrchst" in user_jvm

    # InstallArtifact reflects the result
    assert art.entrypoint == "run.sh"
    assert installer in art.extra_files


async def test_install_raises_if_installer_doesnt_produce_run_sh(
    tmp_path: Path, monkeypatch,
):
    """If NeoForge's installer fails silently and leaves no run.sh, we
    should not pretend the install succeeded."""
    def fake_run(*, workdir, args, image=None, **_):
        # Drop the installer jar but DON'T produce run.sh.
        (workdir / "user_jvm_args.txt").write_text("")
        return ""

    monkeypatch.setattr("ndrchst.platforms.neoforge.run_jdk_jar", fake_run)

    client = httpx.AsyncClient(transport=httpx.MockTransport(_make_handler()))
    nf = NeoForge(client=client)
    with pytest.raises(ji_mod.JvmInstallError, match=r"run\.sh"):
        await nf.install("21.11.42", tmp_path / "srv")
    await client.aclose()


async def test_install_propagates_runner_errors(tmp_path: Path, monkeypatch):
    """A JvmInstallError from the runner (non-zero exit) should bubble up."""
    def fake_run(*, workdir, args, image=None, **_):
        raise ji_mod.JvmInstallError("installer crashed")

    monkeypatch.setattr("ndrchst.platforms.neoforge.run_jdk_jar", fake_run)

    client = httpx.AsyncClient(transport=httpx.MockTransport(_make_handler()))
    nf = NeoForge(client=client)
    with pytest.raises(ji_mod.JvmInstallError, match="crashed"):
        await nf.install("21.11.42", tmp_path / "srv")
    await client.aclose()
