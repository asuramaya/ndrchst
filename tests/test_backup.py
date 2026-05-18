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


def test_create_safety_writes_prefixed_snapshot(tmp_path: Path):
    data = tmp_path / "data"
    backups = tmp_path / "backups"
    data.mkdir()
    _seed(data)

    snap = b.create_safety(
        server_id="abc", data_dir=data, reason="pre_install", root=backups,
    )
    assert snap is not None
    assert snap.name.startswith(b.SAFETY_PREFIX)
    assert "pre_install" in snap.name
    assert snap.is_safety
    assert snap.safety_reason == "pre_install"


def test_create_safety_returns_none_for_missing_dir(tmp_path: Path):
    backups = tmp_path / "backups"
    snap = b.create_safety(
        server_id="abc", data_dir=tmp_path / "does-not-exist",
        reason="pre_install", root=backups,
    )
    assert snap is None


def test_safety_snapshots_appear_in_list_alongside_user_backups(tmp_path: Path):
    data = tmp_path / "data"
    backups = tmp_path / "backups"
    data.mkdir()
    _seed(data)

    user_b = b.create(server_id="abc", data_dir=data, root=backups)
    safety = b.create_safety(
        server_id="abc", data_dir=data, reason="pre_install", root=backups,
    )

    names = {b.name for b in b.list_for("abc", root=backups)}
    assert user_b.name in names
    assert safety.name in names


def test_safety_rotation_trims_oldest_keeping_user_backups(tmp_path: Path, monkeypatch):
    """Rotation must never delete user-created backups, even old ones."""
    data = tmp_path / "data"
    backups = tmp_path / "backups"
    data.mkdir()
    _seed(data)

    # One real user backup first — should survive any number of safety snapshots
    user = b.create(server_id="abc", data_dir=data, root=backups)

    # Patch the timestamp granularity so successive snapshots get unique names
    import datetime as dt
    counter = {"n": 0}
    real_now = dt.datetime.now
    class FakeDT(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            counter["n"] += 1
            return real_now(tz) + dt.timedelta(seconds=counter["n"])
    monkeypatch.setattr(b, "datetime", FakeDT)

    for _ in range(8):
        b.create_safety(
            server_id="abc", data_dir=data, reason="pre_install",
            root=backups, keep=3,
        )

    listed = b.list_for("abc", root=backups)
    safeties = [x for x in listed if x.is_safety]
    user_backups = [x for x in listed if not x.is_safety]
    assert len(safeties) == 3, f"expected 3 safeties after trim, got {[s.name for s in safeties]}"
    assert any(x.name == user.name for x in user_backups), "user backup must survive rotation"


def test_safety_reason_special_chars_sanitized(tmp_path: Path):
    data = tmp_path / "data"
    backups = tmp_path / "backups"
    data.mkdir()
    _seed(data)
    snap = b.create_safety(
        server_id="abc", data_dir=data, reason="pre install/restore",
        root=backups,
    )
    assert snap is not None
    # No path separator should leak into the filename
    assert "/" not in snap.name
