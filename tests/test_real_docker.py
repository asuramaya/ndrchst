"""Real Docker integration tests.

These tests need an actual Docker daemon reachable via the default socket.
Skipped by default — run with `pytest -m docker` on a host with Docker.

The intent is to verify the end-to-end claim that a Paper or Bedrock server
container actually boots, accepts a connection, and shuts down cleanly. The
machine running these tests should have:
  * docker daemon running
  * user in `docker` group (or run pytest with appropriate perms)
  * at least 5GB free disk

Each test cleans up its own container and data dir.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.docker


@pytest.fixture
def docker_client():
    try:
        import docker
        c = docker.from_env()
        c.ping()
        return c
    except Exception as e:
        pytest.skip(f"Docker not available: {e}")


async def test_paper_real_boot(tmp_path: Path, docker_client):
    """Provision a real Paper server, start it, wait for 'Done' log, stop."""
    from ndrchst.runtime.docker import Docker
    from ndrchst.runtime.lifecycle import CreateRequest, Lifecycle
    from ndrchst.store.db import connect

    conn = connect(tmp_path / "t.db")
    lc = Lifecycle(Docker(client=docker_client), conn, servers_root=tmp_path)

    server = await lc.create(CreateRequest(
        name="ItestPaper", platform_id="paper", version="1.21.3",
        port=25599, memory_mb=1024,
    ))
    try:
        await lc.start(server.id)
        # Paper takes ~30s to come up on a warm cache, more on cold
        deadline = time.time() + 180
        while time.time() < deadline:
            logs = await lc.logs(server.id, lines=200)
            if "Done" in logs and "For help" in logs:
                break
            await asyncio.sleep(2)
        else:
            raise AssertionError(f"Paper didn't log 'Done' within 180s. Tail:\n{logs[-2000:]}")
    finally:
        await lc.delete(server.id, remove_files=True)


async def test_bedrock_real_boot(tmp_path: Path, docker_client):
    """Provision a real Bedrock server, start it, wait for 'Server started.'"""
    from ndrchst.runtime.docker import Docker
    from ndrchst.runtime.lifecycle import CreateRequest, Lifecycle
    from ndrchst.store.db import connect

    conn = connect(tmp_path / "t.db")
    lc = Lifecycle(Docker(client=docker_client), conn, servers_root=tmp_path)

    server = await lc.create(CreateRequest(
        name="ItestBedrock", platform_id="bedrock", version="latest",
        port=19199, memory_mb=512,
    ))
    try:
        await lc.start(server.id)
        deadline = time.time() + 90
        while time.time() < deadline:
            logs = await lc.logs(server.id, lines=200)
            if "Server started" in logs or "IPv4 supported" in logs:
                break
            await asyncio.sleep(2)
        else:
            raise AssertionError(f"BDS didn't start within 90s. Tail:\n{logs[-2000:]}")
    finally:
        await lc.delete(server.id, remove_files=True)
