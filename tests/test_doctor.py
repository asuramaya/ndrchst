"""Doctor command tests."""
from __future__ import annotations

from ndrchst.doctor import (
    check_disk_space,
    check_docker_module,
    check_port_free,
    check_python,
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
