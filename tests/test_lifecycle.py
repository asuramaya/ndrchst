"""Lifecycle integration: platform install (faked) + docker (faked) + SQLite (real).

This is the highest-coverage test in the suite — exercises the same code path
that the API will hit when a user clicks "Create server".

Stresses:
  - Java + Bedrock end-to-end (create, start, stop, delete)
  - validation: bad name, bad port, duplicate port, unknown platform
  - port-protocol mapping (TCP for Java, UDP for Bedrock) propagated to docker
  - cleanup on delete (container removed, db record gone)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ndrchst.domain.models import Family, ServerStatus
from ndrchst.platforms import REGISTRY as PLATFORMS
from ndrchst.platforms.base import InstallArtifact
from ndrchst.runtime.docker import Docker
from ndrchst.runtime.lifecycle import CreateRequest, Lifecycle, LifecycleError
from ndrchst.store import servers as srv_store
from ndrchst.store.db import connect

# Re-use the fake docker client from the docker runtime test
from tests.test_docker_runtime import FakeClient


@pytest.fixture
def lifecycle(tmp_path: Path, monkeypatch) -> Lifecycle:
    # Stub platform.install on every registered platform so we don't hit the network
    for p in PLATFORMS.values():
        async def fake_install(version, dest, *, _p=p):
            dest.mkdir(parents=True, exist_ok=True)
            if _p.family is Family.JAVA:
                (dest / "server.jar").write_bytes(b"PK\x03\x04fake")
                entry = "server.jar"
            else:
                (dest / "bedrock_server").write_bytes(b"\x7fELFfake")
                entry = "bedrock_server"
            return InstallArtifact(path=dest, entrypoint=entry)
        monkeypatch.setattr(p, "install", fake_install)

    conn = connect(tmp_path / "lc.db")
    docker = Docker(client=FakeClient())
    return Lifecycle(docker, conn, servers_root=tmp_path / "servers")


async def test_create_java_paper(lifecycle: Lifecycle):
    server = await lifecycle.create(CreateRequest(
        name="Survival",
        platform_id="paper",
        version="1.21.3",
        port=25565,
        memory_mb=2048,
    ))
    assert server.family is Family.JAVA
    assert server.container_id is not None
    assert server.status is ServerStatus.CREATED
    # platform install actually ran
    assert (lifecycle._root / server.id / "server.jar").exists()


async def test_create_bedrock_first_class(lifecycle: Lifecycle):
    server = await lifecycle.create(CreateRequest(
        name="My Bedrock",
        platform_id="bedrock",
        version="latest",
        port=19132,
        memory_mb=1024,
    ))
    assert server.family is Family.BEDROCK
    assert server.container_id is not None
    assert (lifecycle._root / server.id / "bedrock_server").exists()


async def test_lifecycle_state_transitions(lifecycle: Lifecycle):
    server = await lifecycle.create(CreateRequest(
        name="World", platform_id="paper", version="1.21.3", port=25565, memory_mb=2048
    ))

    await lifecycle.start(server.id)
    assert srv_store.get(lifecycle._conn, server.id).status is ServerStatus.RUNNING

    await lifecycle.stop(server.id)
    assert srv_store.get(lifecycle._conn, server.id).status is ServerStatus.STOPPED


async def test_delete_removes_container_and_record(lifecycle: Lifecycle):
    server = await lifecycle.create(CreateRequest(
        name="ToDelete", platform_id="paper", version="1.21.3", port=25565, memory_mb=2048
    ))
    data_dir = lifecycle._root / server.id
    assert data_dir.exists()

    await lifecycle.delete(server.id, remove_files=True)
    assert srv_store.get(lifecycle._conn, server.id) is None
    assert not data_dir.exists()


async def test_delete_keeps_files_by_default(lifecycle: Lifecycle):
    server = await lifecycle.create(CreateRequest(
        name="Keep", platform_id="paper", version="1.21.3", port=25566, memory_mb=2048
    ))
    data_dir = lifecycle._root / server.id
    await lifecycle.delete(server.id)
    assert not srv_store.get(lifecycle._conn, server.id)
    assert data_dir.exists()  # files preserved by default


async def test_validation_rejects_bad_name(lifecycle: Lifecycle):
    with pytest.raises(LifecycleError, match="server name"):
        await lifecycle.create(CreateRequest(
            name="../etc/passwd",
            platform_id="paper", version="1.21.3", port=25565, memory_mb=2048,
        ))


async def test_validation_rejects_unknown_platform(lifecycle: Lifecycle):
    with pytest.raises(LifecycleError, match="unknown platform"):
        await lifecycle.create(CreateRequest(
            name="x", platform_id="not-real", version="1", port=25565, memory_mb=2048,
        ))


async def test_validation_rejects_privileged_port(lifecycle: Lifecycle):
    with pytest.raises(LifecycleError, match="port must be"):
        await lifecycle.create(CreateRequest(
            name="x", platform_id="paper", version="1.21.3", port=80, memory_mb=2048,
        ))


async def test_validation_rejects_low_memory(lifecycle: Lifecycle):
    with pytest.raises(LifecycleError, match="memory must be"):
        await lifecycle.create(CreateRequest(
            name="x", platform_id="paper", version="1.21.3", port=25565, memory_mb=256,
        ))


async def test_validation_rejects_duplicate_port(lifecycle: Lifecycle):
    await lifecycle.create(CreateRequest(
        name="First", platform_id="paper", version="1.21.3", port=25565, memory_mb=2048,
    ))
    with pytest.raises(LifecycleError, match="already used"):
        await lifecycle.create(CreateRequest(
            name="Second", platform_id="paper", version="1.21.3", port=25565, memory_mb=2048,
        ))


async def test_lifecycle_on_nonexistent_server_raises(lifecycle: Lifecycle):
    with pytest.raises(LifecycleError, match="not found"):
        await lifecycle.start("nonexistent")


async def test_create_stub_platform_raises_clean_lifecycle_error(
    tmp_path: Path, monkeypatch,
):
    """Stub platforms raise NotImplementedError from install(); lifecycle
    must convert that to a LifecycleError so API/web surface clean 4xx
    instead of a 500. Also: half-created data dir must be cleaned up."""
    # Don't monkeypatch — let vanilla's real (stub) install() run
    conn = connect(tmp_path / "lc.db")
    lc = Lifecycle(Docker(client=FakeClient()), conn, servers_root=tmp_path / "servers")
    with pytest.raises(LifecycleError, match="not yet implemented"):
        await lc.create(CreateRequest(
            name="StubAttempt", platform_id="vanilla",
            version="1.21.3", port=25599, memory_mb=2048,
        ))
    # No half-created data dir left behind
    assert not list((tmp_path / "servers").iterdir()) if (tmp_path / "servers").exists() else True


async def test_cross_play_reserves_bedrock_bridge_port_and_exposes_udp(
    lifecycle: Lifecycle, monkeypatch,
):
    """cross_play=True on Java must:
      1. record bedrock_bridge_port on the Server,
      2. exposes BOTH 25565/tcp (Paper) and 19132/udp (Geyser) on the container,
      3. reserve the bridge port so a second server can't reuse it.
    """
    # Stub geyser install since we're not exercising the download path here
    monkeypatch.setattr(
        "ndrchst.runtime.lifecycle.install_cross_play",
        lambda *a, **kw: __import__("asyncio").sleep(0),
    )
    s = await lifecycle.create(CreateRequest(
        name="CP", platform_id="paper", version="1.21.3",
        port=25565, memory_mb=2048, cross_play=True,
        bedrock_bridge_port=59150,
    ))
    assert s.bedrock_bridge_port == 59150

    # The container spec exposes both protocols
    from ndrchst.runtime.lifecycle import _build_spec
    spec = _build_spec(s, lifecycle._root / s.id)
    assert "25565/tcp" in spec.ports
    assert "19132/udp" in spec.ports
    assert spec.ports["19132/udp"] == 59150

    # A second cross-play server can't reuse the bridge port
    with pytest.raises(LifecycleError, match=r"bedrock_bridge_port 59150 already used"):
        await lifecycle.create(CreateRequest(
            name="CP2", platform_id="paper", version="1.21.3",
            port=25566, memory_mb=2048, cross_play=True,
            bedrock_bridge_port=59150,
        ))


async def test_cross_play_rejects_same_port_for_java_and_bridge(lifecycle: Lifecycle):
    with pytest.raises(LifecycleError, match="must differ from the Java port"):
        await lifecycle.create(CreateRequest(
            name="Same", platform_id="paper", version="1.21.3",
            port=25565, memory_mb=2048, cross_play=True,
            bedrock_bridge_port=25565,
        ))


async def test_version_latest_resolves_to_concrete(lifecycle: Lifecycle, monkeypatch):
    """version='latest' must resolve to whatever platform.versions()[0] is."""
    from ndrchst.platforms.base import VersionInfo

    async def fake_versions():
        return [VersionInfo(version="1.21.4-resolved"), VersionInfo(version="1.21.3")]

    paper = next(p for p in __import__("ndrchst.platforms", fromlist=["REGISTRY"]).REGISTRY.values() if p.id == "paper")
    monkeypatch.setattr(paper, "versions", fake_versions)

    s = await lifecycle.create(CreateRequest(
        name="LatestPaper", platform_id="paper", version="latest",
        port=25580, memory_mb=2048,
    ))
    assert s.version == "1.21.4-resolved"


async def test_build_spec_neoforge_uses_run_sh_and_java_tool_options(monkeypatch):
    """NeoForge servers boot via the installer-produced run.sh; memory has
    to ride on JAVA_TOOL_OPTIONS because we can't intercept run.sh's @-args."""
    from ndrchst.domain.models import Family, Server
    from ndrchst.runtime.lifecycle import _build_spec
    s = Server(
        id="nf-test", name="ATM", platform_id="neoforge", family=Family.JAVA,
        version="21.11.42", port=25565, memory_mb=8192,
        extra_jvm_flags="-XX:+UseG1GC",
    )
    spec = _build_spec(s, Path("/tmp/srv"))
    assert spec.cmd == ["bash", "run.sh", "nogui"]
    # Memory + user flags merged into JAVA_TOOL_OPTIONS so every JVM start
    # within the container picks them up (run.sh + any mod-bootstrap re-exec).
    jto = spec.env["JAVA_TOOL_OPTIONS"]
    assert "-Xmx8192m" in jto
    assert "-Xms4096m" in jto
    assert "-XX:+UseG1GC" in jto
    # EULA is still set (NeoForge writes eula.txt; env var is harmless)
    assert spec.env["EULA"] == "TRUE"


async def test_build_spec_modpack_uses_run_sh_like_neoforge(monkeypatch):
    """Modpack servers layer on NeoForge — same `run.sh` entrypoint, same
    JAVA_TOOL_OPTIONS memory plumbing as plain NeoForge."""
    from ndrchst.domain.models import Family, Server
    from ndrchst.runtime.lifecycle import _build_spec
    s = Server(
        id="mp-test", name="ATM10", platform_id="modpack", family=Family.JAVA,
        version="http://127.0.0.1:9999/p.zip", port=25590, memory_mb=10240,
    )
    spec = _build_spec(s, Path("/tmp/srv"))
    assert spec.cmd == ["bash", "run.sh", "nogui"]
    assert "-Xmx10240m" in spec.env["JAVA_TOOL_OPTIONS"]
    assert "-Xms5120m" in spec.env["JAVA_TOOL_OPTIONS"]


async def test_build_spec_paper_still_uses_direct_java(monkeypatch):
    """Paper's spec is unchanged — direct `java -jar server.jar` invocation,
    no JAVA_TOOL_OPTIONS sidecar (memory rides on the cmdline directly)."""
    from ndrchst.domain.models import Family, Server
    from ndrchst.runtime.lifecycle import _build_spec
    s = Server(
        id="pp-test", name="P", platform_id="paper", family=Family.JAVA,
        version="1.21.3", port=25565, memory_mb=2048,
    )
    spec = _build_spec(s, Path("/tmp/srv"))
    assert "-Xmx2048m" in spec.cmd
    assert spec.cmd[-3:] == ["-jar", "server.jar", "nogui"]
    assert "JAVA_TOOL_OPTIONS" not in spec.env


async def test_create_paper_upstream_404_raises_clean_lifecycle_error(
    tmp_path: Path, monkeypatch,
):
    """Paper API returns 404 for unknown versions; httpx raises HTTPStatusError.
    Lifecycle must convert that to a 'version not found' LifecycleError."""
    import httpx

    from ndrchst.platforms import REGISTRY as PLATFORMS

    async def install_404(version, dest):
        # Simulate what Paper's real install does when version is bogus:
        # r.raise_for_status() on a 404
        resp = httpx.Response(404, request=httpx.Request("GET", "https://api.papermc.io/x"))
        raise httpx.HTTPStatusError("404", request=resp.request, response=resp)
    monkeypatch.setattr(PLATFORMS["paper"], "install", install_404)

    conn = connect(tmp_path / "lc.db")
    lc = Lifecycle(Docker(client=FakeClient()), conn, servers_root=tmp_path / "servers")
    with pytest.raises(LifecycleError, match=r"version '99\.99\.99' not found"):
        await lc.create(CreateRequest(
            name="BadVer", platform_id="paper",
            version="99.99.99", port=25598, memory_mb=2048,
        ))
