"""Backup create/list/restore/delete tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from ndrchst.runtime import backup as b


def _seed(data_dir: Path) -> None:
    (data_dir / "world").mkdir(parents=True)
    (data_dir / "world" / "level.dat").write_bytes(b"NBT-fake-bytes")
    (data_dir / "server.properties").write_text("motd=Hi\n")


def test_create_then_list(tmp_path: Path):
    data = tmp_path / "data"
    backups = tmp_path / "backups"
    data.mkdir()
    _seed(data)

    b1 = b.create(server_id="abc", data_dir=data, root=backups)
    assert b1.path.exists()
    assert b1.size > 0

    listed = b.list_for("abc", root=backups)
    assert len(listed) == 1
    assert listed[0].name == b1.name


def test_restore_round_trip(tmp_path: Path):
    data = tmp_path / "data"
    backups = tmp_path / "backups"
    data.mkdir()
    _seed(data)

    snapshot = b.create(server_id="abc", data_dir=data, root=backups)
    # Mutate the data dir after backup
    (data / "world" / "level.dat").write_bytes(b"NEW-DATA")
    (data / "extra.txt").write_text("added after backup")

    b.restore(server_id="abc", name=snapshot.name, data_dir=data, root=backups)

    assert (data / "world" / "level.dat").read_bytes() == b"NBT-fake-bytes"
    # restore wipes-and-restores: post-backup additions should be gone
    assert not (data / "extra.txt").exists()


def test_delete(tmp_path: Path):
    data = tmp_path / "data"
    backups = tmp_path / "backups"
    data.mkdir()
    _seed(data)
    snap = b.create(server_id="abc", data_dir=data, root=backups)
    b.delete(server_id="abc", name=snap.name, root=backups)
    assert b.list_for("abc", root=backups) == []


def test_restore_missing_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        b.restore(server_id="abc", name="nope.tar.gz", data_dir=tmp_path, root=tmp_path / "b")


def test_size_human():
    bk = b.Backup(server_id="x", name="t.tar.gz", path=Path("/"), size=1536)
    assert bk.size_human == "1.5K"
