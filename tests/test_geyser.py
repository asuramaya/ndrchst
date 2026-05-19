"""Geyser cross-play install tests + integration with lifecycle."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from ndrchst.domain.models import Family
from ndrchst.platforms import REGISTRY as PLATFORMS
from ndrchst.platforms.base import InstallArtifact
from ndrchst.runtime.docker import Docker
from ndrchst.runtime.geyser import (
    _spigot_artifact_url,
    install_cross_play,
)
from ndrchst.runtime.lifecycle import CreateRequest, Lifecycle, LifecycleError
from ndrchst.store.db import connect
from tests.test_docker_runtime import FakeClient


def _handler() -> tuple[list[httpx.Request], callable]:
    captured: list[httpx.Request] = []

    def h(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        url = str(request.url)
        if url == _spigot_artifact_url("geyser"):
            return httpx.Response(200, content=b"PK\x03\x04geyser-jar-bytes")
        if url == _spigot_artifact_url("floodgate"):
            return httpx.Response(200, content=b"PK\x03\x04floodgate-jar-bytes")
        return httpx.Response(404)
    return captured, h


async def test_install_writes_both_jars_and_config(tmp_path: Path):
    _, h = _handler()
    client = httpx.AsyncClient(transport=httpx.MockTransport(h))

    await install_cross_play(tmp_path, java_port=25577, bedrock_port=19140, client=client)

    plugins = tmp_path / "plugins"
    assert (plugins / "Geyser-Spigot.jar").read_bytes().startswith(b"PK\x03\x04geyser")
    assert (plugins / "Floodgate-Spigot.jar").read_bytes().startswith(b"PK\x03\x04floodgate")

    cfg = (plugins / "Geyser-Spigot" / "config.yml").read_text()
    assert "port: 19140" in cfg
    assert "port: 25577" in cfg
    assert "auth-type: floodgate" in cfg

    await client.aclose()


async def test_install_is_idempotent(tmp_path: Path):
    _, h = _handler()
    client = httpx.AsyncClient(transport=httpx.MockTransport(h))
    await install_cross_play(tmp_path, client=client)
    await install_cross_play(tmp_path, client=client)  # second call must not error
    await client.aclose()


async def test_install_seeds_online_mode_false_for_fresh_data_dir(tmp_path: Path):
    """Floodgate requires online-mode=false. With no pre-existing
    server.properties we pre-create a minimal one with the right flags."""
    _, h = _handler()
    client = httpx.AsyncClient(transport=httpx.MockTransport(h))
    await install_cross_play(tmp_path, client=client)
    props = (tmp_path / "server.properties").read_text()
    assert "online-mode=false" in props
    assert "enforce-secure-profile=false" in props
    await client.aclose()


async def test_install_flips_existing_server_properties(tmp_path: Path):
    """If Paper has already generated server.properties with the wrong
    flags, install_cross_play must rewrite them in place — preserving
    every other key + comment."""
    props_path = tmp_path / "server.properties"
    props_path.write_text(
        "# Minecraft server properties\n"
        "motd=Welcome!\n"
        "online-mode=true\n"
        "max-players=20\n"
        "enforce-secure-profile=true\n"
        "level-name=world\n"
    )
    _, h = _handler()
    client = httpx.AsyncClient(transport=httpx.MockTransport(h))
    await install_cross_play(tmp_path, client=client)
    text = props_path.read_text()
    assert "online-mode=false" in text
    assert "online-mode=true" not in text
    assert "enforce-secure-profile=false" in text
    # untouched keys + comment survive
    assert "motd=Welcome!" in text
    assert "max-players=20" in text
    assert "# Minecraft server properties" in text
    assert "level-name=world" in text
    await client.aclose()


# ─── lifecycle integration ──────────────────────────────────────────────────


@pytest.fixture
def lifecycle_with_geyser(tmp_path: Path, monkeypatch) -> Lifecycle:
    # Fake platform install
    for p in PLATFORMS.values():
        async def fake_install(version, dest, *, _p=p):
            dest.mkdir(parents=True, exist_ok=True)
            if _p.family is Family.JAVA:
                (dest / "server.jar").write_bytes(b"fake")
                return InstallArtifact(path=dest, entrypoint="server.jar")
            (dest / "bedrock_server").write_bytes(b"fake")
            return InstallArtifact(path=dest, entrypoint="bedrock_server")
        monkeypatch.setattr(p, "install", fake_install)

    # Patch the geyser install to a recorder so we don't hit the network here
    recorded: dict = {}
    async def fake_install_cross_play(data_dir, *, java_port=25565, bedrock_port=19132, client=None):
        recorded["data_dir"] = data_dir
        recorded["java_port"] = java_port
        recorded["bedrock_port"] = bedrock_port
    monkeypatch.setattr("ndrchst.runtime.lifecycle.install_cross_play", fake_install_cross_play)

    conn = connect(tmp_path / "lc.db")
    lc = Lifecycle(Docker(client=FakeClient()), conn, servers_root=tmp_path / "servers")
    lc._recorded = recorded  # type: ignore[attr-defined]
    return lc


async def test_cross_play_triggers_geyser_for_java(lifecycle_with_geyser: Lifecycle):
    s = await lifecycle_with_geyser.create(CreateRequest(
        name="CrossPlay", platform_id="paper", version="1.21.3",
        port=25565, memory_mb=2048, cross_play=True,
    ))
    rec = lifecycle_with_geyser._recorded  # type: ignore[attr-defined]
    assert rec["java_port"] == 25565
    assert rec["data_dir"] == lifecycle_with_geyser._root / s.id


async def test_cross_play_rejected_for_bedrock(lifecycle_with_geyser: Lifecycle):
    with pytest.raises(LifecycleError, match="only meaningful for Java"):
        await lifecycle_with_geyser.create(CreateRequest(
            name="WrongFamily", platform_id="bedrock", version="latest",
            port=19132, memory_mb=2048, cross_play=True,
        ))


async def test_cross_play_off_does_not_install_geyser(lifecycle_with_geyser: Lifecycle):
    await lifecycle_with_geyser.create(CreateRequest(
        name="JavaOnly", platform_id="paper", version="1.21.3",
        port=25565, memory_mb=2048, cross_play=False,
    ))
    # recorded dict should be empty
    assert lifecycle_with_geyser._recorded == {}  # type: ignore[attr-defined]
