"""Doctor command tests."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ndrchst import doctor
from ndrchst.doctor import (
    check_disk_space,
    check_docker_module,
    check_port_free,
    check_python,
    check_registered_server_ports,
)


def test_python_check_passes_on_312_or_higher():
    r = check_python()
    assert r.ok
    assert "." in r.detail


def test_docker_module_is_importable():
    r = check_docker_module()
    assert r.ok


def test_disk_space_huge_threshold_fails():
    # Asking for 9999 PB should fail on any normal machine
    r = check_disk_space(min_gb=9999 * 1024 * 1024)
    assert not r.ok
    assert "free" in r.detail


def test_port_free_check_unused_port():
    r = check_port_free(53999)
    assert r.ok


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch):
    db = tmp_path / "ndrchst.db"
    monkeypatch.setattr(doctor, "DEFAULT_DB_PATH", db)
    return db


def test_registered_ports_skipped_when_no_db(isolated_db):
    results = check_registered_server_ports()
    assert len(results) == 1
    assert results[0].ok
    assert "no DB" in results[0].detail


def _make_db_with(rows, db_path: Path):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE servers (name TEXT, family TEXT, port INTEGER)"
    )
    conn.executemany("INSERT INTO servers VALUES (?, ?, ?)", rows)
    conn.commit()
    conn.close()


def test_registered_ports_empty_db_returns_friendly_message(isolated_db):
    _make_db_with([], isolated_db)
    results = check_registered_server_ports()
    assert len(results) == 1
    assert results[0].ok
    assert "no servers" in results[0].detail


def test_registered_ports_probes_each_server_with_correct_protocol(isolated_db):
    # Use high unused ports — they should probe as "free"
    _make_db_with(
        [("Java1", "java", 54123), ("Bdrk1", "bedrock", 54124)],
        isolated_db,
    )
    results = check_registered_server_ports()
    assert len(results) == 2
    # Java entry mentions TCP
    java = next(r for r in results if "Java1" in r.name)
    assert "TCP" in java.detail and "java" in java.detail
    # Bedrock entry mentions UDP
    bdrk = next(r for r in results if "Bdrk1" in r.name)
    assert "UDP" in bdrk.detail and "bedrock" in bdrk.detail


def test_registered_ports_handles_unknown_family(isolated_db):
    _make_db_with([("Weird", "klingon", 54125)], isolated_db)
    results = check_registered_server_ports()
    assert len(results) == 1
    assert not results[0].ok
    assert "unknown family" in results[0].detail
