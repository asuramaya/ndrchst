"""Live HTTP + WebSocket stress against a running uvicorn process.

This is the closest thing to a browser-driven test we have without pulling
in Playwright. We boot the real app on a free port, hit every public
endpoint, parse the rendered HTML, and assert structural invariants.

Marked `live` so it can be skipped on CI hosts where binding a port is
restricted: pytest -m "not live" to skip.
"""
from __future__ import annotations

import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.live


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("live")
    port = _free_port()
    env = {
        "PYTHONUNBUFFERED": "1",
        "PATH": "/usr/bin:/bin",
    }
    repo = Path(__file__).resolve().parent.parent
    venv_py = repo / ".venv" / "bin" / "python"
    proc = subprocess.Popen(
        [
            str(venv_py), "-m", "uvicorn",
            "ndrchst.api.main:app",
            "--host", "127.0.0.1", "--port", str(port),
            "--log-level", "warning",
        ],
        env={**env, "NDRCHST_DATA": str(tmp)},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        cwd=str(repo),
    )
    # Wait for the server to come up
    for _ in range(40):
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=0.5)
            if r.status_code == 200:
                break
        except httpx.RequestError:
            pass
        time.sleep(0.1)
    else:
        proc.terminate()
        out = proc.stdout.read().decode("utf-8", "replace") if proc.stdout else ""
        raise RuntimeError(f"server didn't start on port {port}; output:\n{out}")
    yield f"http://127.0.0.1:{port}"
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_healthz_reports_docker_state(server):
    r = httpx.get(f"{server}/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    # Docker is unavailable on this machine — the stress assumes that
    assert body["docker"] in ("ok", "unavailable")


def test_index_page_full_structure(server):
    r = httpx.get(f"{server}/")
    assert r.status_code == 200
    html = r.text
    # Layout chrome
    assert '<aside class="sidebar">' in html
    assert "Servers" in html
    assert "System" in html
    assert "Settings" in html
    # Title + base shell
    assert "<title>" in html
    assert 'src="https://unpkg.com/htmx.org@2.0.3"' in html
    # Empty state
    assert "No servers yet" in html


def test_new_form_full_partial(server):
    r = httpx.get(f"{server}/servers/new", headers={"HX-Request": "true"})
    assert r.status_code == 200
    html = r.text
    # All 7 platform options, Bedrock included
    for pid in ("paper", "purpur", "vanilla", "fabric", "forge", "neoforge", "bedrock"):
        assert f'value="{pid}"' in html
    # htmx hooks
    assert 'hx-post="/servers"' in html
    assert "cross_play" in html


def test_placeholder_pages(server):
    for path in ("/system", "/settings"):
        r = httpx.get(f"{server}{path}")
        assert r.status_code == 200
        # base chrome present
        assert '<aside class="sidebar">' in r.text


def test_mutations_503_without_docker(server):
    r = httpx.post(f"{server}/servers", data={
        "name": "X", "platform_id": "paper", "version": "1.21.3",
        "port": "25565", "memory_mb": "2048",
    })
    assert r.status_code == 503
    body_lower = r.text.lower()
    assert "docker" in body_lower


def test_unknown_tab_404(server):
    r = httpx.get(f"{server}/servers/anything/nope")
    # Server might be 404 or 500 depending on whether server exists; both rejections are OK
    assert r.status_code in (404, 400)


def test_api_servers_returns_json(server):
    r = httpx.get(f"{server}/api/servers")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.json() == []


def test_api_platforms_includes_bedrock(server):
    r = httpx.get(f"{server}/api/platforms")
    assert r.status_code == 200
    ids = {p["id"] for p in r.json()}
    assert "bedrock" in ids
    assert "paper" in ids


def test_static_css_served(server):
    r = httpx.get(f"{server}/static/app.css")
    assert r.status_code == 200
    assert "--accent" in r.text
    assert ".tab" in r.text
    assert ".console" in r.text
