"""Files browser tests — path traversal must be impossible."""
from __future__ import annotations

from pathlib import Path

import pytest

from ndrchst.domain import files


def test_list_dir_basic(tmp_path: Path):
    (tmp_path / "a.txt").write_text("hi")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.json").write_text("{}")
    entries = files.list_dir(tmp_path, "")
    names = [e.name for e in entries]
    # dirs first
    assert names == ["sub", "a.txt"]
    assert entries[0].is_dir
    assert entries[1].editable  # .txt is in editable extensions


def test_traversal_dotdot_rejected(tmp_path: Path):
    (tmp_path / "child").mkdir()
    with pytest.raises(files.PathError):
        files.list_dir(tmp_path, "../etc")


def test_traversal_absolute_rejected(tmp_path: Path):
    with pytest.raises(files.PathError):
        files.list_dir(tmp_path, "/etc")


def test_read_text_size_cap(tmp_path: Path):
    big = "x" * (files.MAX_EDIT_BYTES + 1)
    (tmp_path / "big.txt").write_text(big)
    with pytest.raises(files.PathError, match="too large"):
        files.read_text(tmp_path, "big.txt")


def test_write_text_round_trip(tmp_path: Path):
    files.write_text(tmp_path, "config/settings.yml", "key: value\n")
    assert (tmp_path / "config" / "settings.yml").read_text() == "key: value\n"
    assert files.read_text(tmp_path, "config/settings.yml") == "key: value\n"


def test_editable_detection():
    e_path = Path("eula.txt")
    assert files._editable(e_path.name)
    assert files._editable("server.properties")
    assert files._editable("settings.yml")
    assert not files._editable("server.jar")
    assert not files._editable("world.dat")
