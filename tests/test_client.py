"""Client bundle generation tests."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from ndrchst.domain.models import Family, Server
from ndrchst.runtime import client


def _make_server(**overrides) -> Server:
    defaults = dict(
        id="abc123def456",
        name="Test Server",
        platform_id="paper",
        family=Family.JAVA,
        version="1.21.11",
        port=25574,
        memory_mb=2048,
    )
    defaults.update(overrides)
    return Server(**defaults)


def test_build_bundle_produces_zip_with_pinned_config(tmp_path: Path):
    s = _make_server()
    b = client.build_bundle(
        s, public_host="100.64.0.1", clients_root=tmp_path,
    )
    assert b.zip_path.exists()
    assert b.size > 1000  # non-trivial

    with zipfile.ZipFile(b.zip_path) as zf:
        names = zf.namelist()
        assert "ndrchst_client/config.py" in names
        assert "ndrchst_client/launcher.py" in names
        assert "ndrchst_client/app.py" in names
        assert "ndrchst_client/__main__.py" in names
        assert "requirements.txt" in names
        assert "launch.sh" in names
        assert "launch.bat" in names
        assert "README.txt" in names

        cfg = zf.read("ndrchst_client/config.py").decode()
        assert "SERVER_HOST = '100.64.0.1'" in cfg
        assert "SERVER_PORT = 25574" in cfg
        assert "MC_VERSION = '1.21.11'" in cfg
        assert "SERVER_ID = 'abc123def456'" in cfg


def test_launch_sh_is_executable_in_zip(tmp_path: Path):
    """`./launch.sh` must work after unzip — the Unix +x bit has to be stored
    in the zip (zipfile.writestr defaults to no execute bit)."""
    s = _make_server()
    b = client.build_bundle(s, public_host="x", clients_root=tmp_path)
    with zipfile.ZipFile(b.zip_path) as zf:
        mode = (zf.getinfo("launch.sh").external_attr >> 16) & 0o777
        assert mode & 0o111, f"launch.sh not executable (mode {mode:o})"


def test_build_bundle_writes_config_and_manifest_json(tmp_path: Path):
    s = _make_server()
    b = client.build_bundle(s, public_host="play.example.com", clients_root=tmp_path)
    cfg = json.loads(b.config_path.read_text())
    assert cfg["server_host"] == "play.example.com"
    assert cfg["server_port"] == 25574
    assert cfg["mc_version"] == "1.21.11"

    man = json.loads(b.manifest_path.read_text())
    assert man["server_id"] == s.id
    assert man["sha256"] == b.sha256
    assert man["size"] == b.size


def test_bedrock_server_raises(tmp_path: Path):
    s = _make_server(family=Family.BEDROCK, platform_id="bedrock")
    with pytest.raises(client.ClientBuildError, match="Java-only"):
        client.build_bundle(s, public_host="x", clients_root=tmp_path)


def test_rebuild_overwrites(tmp_path: Path):
    s = _make_server()
    b1 = client.build_bundle(s, public_host="a", clients_root=tmp_path)
    s2 = _make_server(version="1.21.12")
    b2 = client.build_bundle(s2, public_host="a", clients_root=tmp_path)
    assert b1.zip_path == b2.zip_path
    with zipfile.ZipFile(b2.zip_path) as zf:
        cfg = zf.read("ndrchst_client/config.py").decode()
        assert "MC_VERSION = '1.21.12'" in cfg


def test_remove_bundle_cleans_dir(tmp_path: Path):
    s = _make_server()
    client.build_bundle(s, public_host="a", clients_root=tmp_path)
    assert (tmp_path / s.id).exists()
    client.remove_bundle(s.id, clients_root=tmp_path)
    assert not (tmp_path / s.id).exists()


def test_bundle_path_returns_none_when_missing(tmp_path: Path):
    assert client.bundle_path("nope", clients_root=tmp_path) is None


def test_public_host_placeholder_when_empty(tmp_path: Path):
    s = _make_server()
    b = client.build_bundle(s, public_host="", clients_root=tmp_path)
    with zipfile.ZipFile(b.zip_path) as zf:
        cfg = zf.read("ndrchst_client/config.py").decode()
        assert "REPLACE_WITH_SERVER_HOST" in cfg


def test_edge_url_lands_in_manifest_and_readme(tmp_path: Path):
    """When the operator pins NDRCHST_EDGE_URL, the bundle README points
    users at the public surface and the manifest records the same URL so
    a future updater knows where to GET fresh copies."""
    s = _make_server()
    b = client.build_bundle(
        s, public_host="mc.ndrchst.com",
        edge_url="https://play.ndrchst.com", clients_root=tmp_path,
    )
    man = json.loads(b.manifest_path.read_text())
    assert man["host"] == "mc.ndrchst.com"
    assert man["edge_url"] == "https://play.ndrchst.com"
    with zipfile.ZipFile(b.zip_path) as zf:
        readme = zf.read("README.txt").decode()
        assert "https://play.ndrchst.com/client/abc123def456/client.zip" in readme
        assert "mc.ndrchst.com:25574" in readme


def test_edge_url_missing_means_no_update_line(tmp_path: Path):
    """If edge_url isn't set, the README simply omits the 'Grab the latest…'
    line — we don't want a dangling URL fragment."""
    s = _make_server()
    b = client.build_bundle(s, public_host="example.com", clients_root=tmp_path)
    with zipfile.ZipFile(b.zip_path) as zf:
        readme = zf.read("README.txt").decode()
    assert "Grab the latest" not in readme
    assert "/client/" not in readme


def test_build_bundle_stages_modpack_when_missing(tmp_path: Path, monkeypatch):
    """build_bundle self-stages the CF pack so the mods index can resolve to
    CDN — the root-cause fix for the all-origin (1.3 GB) regression. Idempotent:
    a present pack isn't re-downloaded."""
    import contextlib

    import httpx

    calls = {"n": 0}

    @contextlib.contextmanager
    def fake_stream(method, url, **kw):
        calls["n"] += 1

        class _R:
            def raise_for_status(self):
                pass

            def iter_bytes(self, _n):
                yield b"PK\x03\x04fake-pack"

        yield _R()

    monkeypatch.setattr(httpx, "stream", fake_stream)
    s = _make_server()
    client.build_bundle(s, public_host="x", clients_root=tmp_path,
                        modpack_url="https://cdn.example/pack.zip")
    pack = tmp_path / s.id / "modpack.zip"
    assert pack.exists() and pack.read_bytes() == b"PK\x03\x04fake-pack"
    assert calls["n"] == 1
    # Present pack → no re-download on the next build.
    client.build_bundle(s, public_host="x", clients_root=tmp_path,
                        modpack_url="https://cdn.example/pack.zip")
    assert calls["n"] == 1


def test_build_bundle_no_modpack_url_no_download(tmp_path: Path, monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("should not download without modpack_url")))
    s = _make_server()
    client.build_bundle(s, public_host="x", clients_root=tmp_path)  # no modpack_url
    assert not (tmp_path / s.id / "modpack.zip").exists()
