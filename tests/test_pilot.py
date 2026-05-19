"""Pilot bundle generation tests."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from ndrchst.domain.models import Family, Server
from ndrchst.runtime import pilot


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
    b = pilot.build_bundle(
        s, public_host="100.89.8.49", pilots_root=tmp_path,
    )
    assert b.zip_path.exists()
    assert b.size > 1000  # non-trivial

    with zipfile.ZipFile(b.zip_path) as zf:
        names = zf.namelist()
        assert "ndrchst_pilot/config.py" in names
        assert "ndrchst_pilot/launcher.py" in names
        assert "ndrchst_pilot/app.py" in names
        assert "ndrchst_pilot/__main__.py" in names
        assert "requirements.txt" in names
        assert "launch.sh" in names
        assert "launch.bat" in names
        assert "README.txt" in names

        cfg = zf.read("ndrchst_pilot/config.py").decode()
        assert "SERVER_HOST = '100.89.8.49'" in cfg
        assert "SERVER_PORT = 25574" in cfg
        assert "MC_VERSION = '1.21.11'" in cfg
        assert "SERVER_ID = 'abc123def456'" in cfg


def test_build_bundle_writes_config_and_manifest_json(tmp_path: Path):
    s = _make_server()
    b = pilot.build_bundle(s, public_host="play.example.com", pilots_root=tmp_path)
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
    with pytest.raises(pilot.PilotBuildError, match="Java-only"):
        pilot.build_bundle(s, public_host="x", pilots_root=tmp_path)


def test_rebuild_overwrites(tmp_path: Path):
    s = _make_server()
    b1 = pilot.build_bundle(s, public_host="a", pilots_root=tmp_path)
    s2 = _make_server(version="1.21.12")
    b2 = pilot.build_bundle(s2, public_host="a", pilots_root=tmp_path)
    assert b1.zip_path == b2.zip_path
    with zipfile.ZipFile(b2.zip_path) as zf:
        cfg = zf.read("ndrchst_pilot/config.py").decode()
        assert "MC_VERSION = '1.21.12'" in cfg


def test_remove_bundle_cleans_dir(tmp_path: Path):
    s = _make_server()
    pilot.build_bundle(s, public_host="a", pilots_root=tmp_path)
    assert (tmp_path / s.id).exists()
    pilot.remove_bundle(s.id, pilots_root=tmp_path)
    assert not (tmp_path / s.id).exists()


def test_bundle_path_returns_none_when_missing(tmp_path: Path):
    assert pilot.bundle_path("nope", pilots_root=tmp_path) is None


def test_public_host_placeholder_when_empty(tmp_path: Path):
    s = _make_server()
    b = pilot.build_bundle(s, public_host="", pilots_root=tmp_path)
    with zipfile.ZipFile(b.zip_path) as zf:
        cfg = zf.read("ndrchst_pilot/config.py").decode()
        assert "REPLACE_WITH_SERVER_HOST" in cfg
