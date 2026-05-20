"""Store / SQLite tests."""
from __future__ import annotations

from pathlib import Path

from ndrchst.domain.models import Family, Server, ServerStatus
from ndrchst.store import servers as srv_store
from ndrchst.store.db import connect


def _make(port: int = 25565, *, sid: str = "abc", family: Family = Family.JAVA) -> Server:
    return Server(
        id=sid,
        name="My Server",
        platform_id="paper",
        family=family,
        version="1.21.3",
        port=port,
        memory_mb=2048,
    )


def test_schema_bootstraps(tmp_path: Path):
    conn = connect(tmp_path / "test.db")
    tables = [
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    ]
    assert "servers" in tables
    assert "installed_assets" in tables


def test_round_trip_insert_get_list(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    s = _make()
    srv_store.insert(conn, s)
    fetched = srv_store.get(conn, "abc")
    assert fetched is not None
    assert fetched.name == "My Server"
    assert fetched.family is Family.JAVA
    assert fetched.status is ServerStatus.CREATED

    listed = srv_store.list_all(conn)
    assert len(listed) == 1 and listed[0].id == "abc"


def test_update_status_and_container_id(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    srv_store.insert(conn, _make())
    srv_store.set_container_id(conn, "abc", "cont-xyz")
    srv_store.update_status(conn, "abc", ServerStatus.RUNNING)
    s = srv_store.get(conn, "abc")
    assert s.container_id == "cont-xyz"
    assert s.status is ServerStatus.RUNNING


def test_port_in_use(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    srv_store.insert(conn, _make(port=25565, sid="a"))
    srv_store.insert(conn, _make(port=19132, sid="b", family=Family.BEDROCK))
    assert srv_store.port_in_use(conn, 25565)
    assert srv_store.port_in_use(conn, 19132)
    assert not srv_store.port_in_use(conn, 25577)
    # exclude self
    assert not srv_store.port_in_use(conn, 25565, exclude="a")


def test_delete(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    srv_store.insert(conn, _make())
    srv_store.delete(conn, "abc")
    assert srv_store.get(conn, "abc") is None


def test_cf_pack_pin_round_trip(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    srv_store.insert(conn, _make())
    assert srv_store.get(conn, "abc").cf_project_id is None
    srv_store.set_cf_pack(conn, "abc", 925200, 8091114)
    s = srv_store.get(conn, "abc")
    assert s.cf_project_id == 925200
    assert s.cf_file_id == 8091114
    # clearing the pin
    srv_store.set_cf_pack(conn, "abc", None, None)
    assert srv_store.get(conn, "abc").cf_project_id is None


def test_neoforge_version_round_trip(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    srv_store.insert(conn, _make())
    assert srv_store.get(conn, "abc").neoforge_version is None
    srv_store.set_neoforge_version(conn, "abc", "21.1.228")
    assert srv_store.get(conn, "abc").neoforge_version == "21.1.228"
