"""Docker runtime tests using a fake docker client.

Stresses:
  - java_image_for picks Java 17 for <1.20.5, 21 for 1.20.5..25.x, 25 for 26.1+
  - create_container removes a same-named container first (idempotency)
  - status mapping projects docker states to ServerStatus correctly,
    including exited-with-nonzero → CRASHED
  - port protocol is UDP for Bedrock, TCP for Java
  - stats parser computes CPU% the docker-stats way
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from docker.errors import ImageNotFound, NotFound

from ndrchst.domain.models import Family, ServerStatus
from ndrchst.runtime.docker import (
    ContainerSpec,
    Docker,
    _docker_state_to_status,
    _parse_stats,
    java_image_for,
)

# ─── Fakes ──────────────────────────────────────────────────────────────────


class _FakeLogStream:
    """Closeable iterator that mimics docker-py's streaming logs response."""

    def __init__(self, source):
        self._it = source
        self._closed = False

    def __iter__(self):
        return self

    def __next__(self):
        if self._closed:
            raise StopIteration
        return next(self._it)

    def close(self):
        self._closed = True


@dataclass
class FakeContainer:
    id: str
    name: str
    status: str = "created"
    attrs: dict = field(default_factory=dict)
    labels: dict = field(default_factory=dict)
    removed: bool = False
    started: bool = False
    stopped: bool = False

    def start(self):
        self.started = True
        self.status = "running"

    def stop(self, timeout: int = 30):
        self.stopped = True
        self.status = "exited"

    def restart(self, timeout: int = 30):
        self.status = "running"

    def remove(self, force: bool = False):
        self.removed = True

    def logs(self, tail: int = 100, *, stream: bool = False, follow: bool = False):
        if not stream:
            return b"[12:00:00] [Server] Done!\n"
        # Streaming + follow: real docker-py returns an iterable that blocks
        # for new chunks. For tests, surface a pre-canned list and a .close()
        # method so the follow_logs coroutine can shut us down cleanly.
        # `_stream_chunks` may be an empty list (explicit "no output"), so
        # check `is None` rather than truthiness.
        chunks = self.__dict__.get("_stream_chunks")
        if chunks is None:
            chunks = [b"[12:00:00] [Server] Done!\n"]
        return _FakeLogStream(iter(chunks))


@dataclass
class FakeContainers:
    by_id: dict = field(default_factory=dict)
    by_name: dict = field(default_factory=dict)
    create_calls: list = field(default_factory=list)

    def get(self, key: str):
        if key in self.by_id:
            return self.by_id[key]
        if key in self.by_name:
            return self.by_name[key]
        raise NotFound(f"no container {key!r}")

    def create(self, **kwargs) -> FakeContainer:
        self.create_calls.append(kwargs)
        cid = f"id-{len(self.create_calls)}"
        c = FakeContainer(
            id=cid,
            name=kwargs["name"],
            labels=kwargs.get("labels", {}),
            attrs={"State": {}},
        )
        self.by_id[cid] = c
        self.by_name[c.name] = c
        return c

    def list(self, all: bool = True, filters=None):
        return list(self.by_id.values())


@dataclass
class FakeImages:
    """Mirrors the surface of ``client.images`` we touch.

    ``always_present`` makes ``get()`` succeed for any name; tests that want
    to exercise the auto-pull path construct with ``always_present=False``.
    """
    present: set = field(default_factory=set)
    pulled: list = field(default_factory=list)
    always_present: bool = True

    def get(self, name: str):
        if self.always_present or name in self.present:
            return {"name": name}  # docker-py returns an Image; shape doesn't matter
        raise ImageNotFound(f"image {name!r} not present locally")

    def pull(self, name: str):
        self.pulled.append(name)
        self.present.add(name)
        return {"name": name}


class FakeClient:
    def __init__(self, *, images_present: set | None = None):
        self.containers = FakeContainers()
        if images_present is None:
            self.images = FakeImages(always_present=True)
        else:
            self.images = FakeImages(present=set(images_present), always_present=False)

    def ping(self) -> bool:
        return True


# ─── Tests ──────────────────────────────────────────────────────────────────


def test_java_image_cutover():
    assert java_image_for("1.19.4") == "eclipse-temurin:17-jre"
    assert java_image_for("1.20.4") == "eclipse-temurin:17-jre"
    assert java_image_for("1.20.5") == "eclipse-temurin:21-jre"
    assert java_image_for("1.21.3") == "eclipse-temurin:21-jre"
    assert java_image_for("1.21.11") == "eclipse-temurin:21-jre"
    # 2026 year-based scheme (26.1+) requires Java 25
    assert java_image_for("26.1") == "eclipse-temurin:25-jre"
    assert java_image_for("26.1.2") == "eclipse-temurin:25-jre"
    # unparseable falls forward to 21
    assert java_image_for("snapshot-junk") == "eclipse-temurin:21-jre"


def test_status_mapping_includes_crashed():
    assert _docker_state_to_status("running", {}) == ServerStatus.RUNNING
    assert _docker_state_to_status("created", {}) == ServerStatus.CREATED
    assert _docker_state_to_status("exited", {"ExitCode": 0}) == ServerStatus.STOPPED
    assert _docker_state_to_status("exited", {"ExitCode": 137}) == ServerStatus.CRASHED
    assert _docker_state_to_status("dead", {}) == ServerStatus.CRASHED
    assert _docker_state_to_status("restarting", {}) == ServerStatus.STARTING


def test_stats_parses_docker_format():
    raw = {
        "cpu_stats": {
            "cpu_usage": {"total_usage": 2_000_000_000},
            "system_cpu_usage": 10_000_000_000,
            "online_cpus": 4,
        },
        "precpu_stats": {
            "cpu_usage": {"total_usage": 1_000_000_000},
            "system_cpu_usage": 9_000_000_000,
        },
        "memory_stats": {
            "usage": 512 * 1024 * 1024,
            "limit": 4096 * 1024 * 1024,
            "stats": {"cache": 0},
        },
    }
    s = _parse_stats(raw)
    # delta=1e9, sys_delta=1e9, ratio=1.0, *4 cpus *100 = 400%
    assert s.cpu_percent == 400.0
    assert s.memory_used_mb == 512
    assert s.memory_limit_mb == 4096


@pytest.fixture
def spec(tmp_path: Path) -> ContainerSpec:
    return ContainerSpec(
        name="ndrchst-srv-abc",
        image="eclipse-temurin:21-jre",
        cmd=["java", "-Xms1024m", "-Xmx1024m", "-jar", "server.jar", "nogui"],
        workdir="/data",
        data_dir=tmp_path,
        ports={"25565/tcp": 25565},
        memory_mb=1024,
        server_id="abc",
        family=Family.JAVA,
        env={"EULA": "TRUE"},
    )


async def test_create_container_writes_labels_and_ports(spec: ContainerSpec):
    fake = FakeClient()
    d = Docker(client=fake)
    cid = await d.create_container(spec)
    assert cid == "id-1"
    call = fake.containers.create_calls[0]
    assert call["name"] == "ndrchst-srv-abc"
    assert call["labels"]["io.ndrchst.managed"] == "abc"
    assert call["labels"]["io.ndrchst.family"] == "java"
    # Java -> TCP
    assert "25565/tcp" in call["ports"]


async def test_create_container_is_idempotent_on_name(spec: ContainerSpec):
    fake = FakeClient()
    d = Docker(client=fake)
    await d.create_container(spec)
    # Second create with same name should remove the first
    await d.create_container(spec)
    # Two creates total — the same-name guard removed the first
    assert len(fake.containers.create_calls) == 2


async def test_bedrock_ports_use_udp(spec: ContainerSpec, tmp_path: Path):
    bedrock_spec = ContainerSpec(
        name="ndrchst-srv-bdrk",
        image="ubuntu:24.04",
        cmd=["./bedrock_server"],
        workdir="/data",
        data_dir=tmp_path,
        ports={"19132/udp": 19132},
        memory_mb=1024,
        server_id="bdrk",
        family=Family.BEDROCK,
        env={"LD_LIBRARY_PATH": "."},
    )
    fake = FakeClient()
    d = Docker(client=fake)
    await d.create_container(bedrock_spec)
    call = fake.containers.create_calls[0]
    assert "19132/udp" in call["ports"]


async def test_status_returns_stopped_for_missing_container():
    fake = FakeClient()
    d = Docker(client=fake)
    s = await d.status("does-not-exist")
    assert s == ServerStatus.STOPPED


async def test_create_pulls_image_when_missing(spec: ContainerSpec):
    """ImageNotFound should trigger an automatic pull, then create succeeds."""
    fake = FakeClient(images_present=set())  # nothing local
    d = Docker(client=fake)
    cid = await d.create_container(spec)
    assert cid
    assert fake.images.pulled == [spec.image]


async def test_create_skips_pull_when_image_present(spec: ContainerSpec):
    fake = FakeClient(images_present={spec.image})
    d = Docker(client=fake)
    await d.create_container(spec)
    assert fake.images.pulled == []


async def test_lifecycle_calls(spec: ContainerSpec):
    fake = FakeClient()
    d = Docker(client=fake)
    cid = await d.create_container(spec)
    c = fake.containers.by_id[cid]

    await d.start(cid)
    assert c.started and c.status == "running"

    await d.stop(cid)
    assert c.stopped

    logs = await d.logs(cid, lines=10)
    assert "Done" in logs

    await d.remove(cid)
    assert c.removed


async def test_follow_logs_streams_decoded_chunks(spec: ContainerSpec):
    fake = FakeClient()
    d = Docker(client=fake)
    cid = await d.create_container(spec)
    c = fake.containers.by_id[cid]
    c._stream_chunks = [b"line 1\n", b"line 2\n", b"line 3\n"]

    received = []
    async for chunk in d.follow_logs(cid, tail=10):
        received.append(chunk)
    # All three lines pumped through, in order
    assert received == ["line 1\n", "line 2\n", "line 3\n"]


async def test_follow_logs_empty_when_container_emits_nothing(spec: ContainerSpec):
    """Container with no output: generator completes cleanly, no hangs."""
    fake = FakeClient()
    d = Docker(client=fake)
    cid = await d.create_container(spec)
    c = fake.containers.by_id[cid]
    c._stream_chunks = []  # empty stream

    received = []
    async for chunk in d.follow_logs(cid, tail=10):
        received.append(chunk)
    assert received == []
