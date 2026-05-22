"""The operator runtime-config surface: knobs resolve override > env > default,
``set`` clamps + persists, the bridge-gated endpoints expose get/set, and the
daily cooldown knob actually changes claim behaviour. (conftest isolates the
overrides file + clears overrides per test.)
"""
from __future__ import annotations

from ndrchst.runtime import op_config
from ndrchst.store import daily_claims as dc
from ndrchst.store.db import connect


def test_get_falls_back_env_then_default(monkeypatch):
    # default
    assert op_config.get("daily_cooldown_s") == 24 * 3600
    # env override
    monkeypatch.setenv("NDRCHST_DAILY_COOLDOWN_S", "3600")
    assert op_config.get("daily_cooldown_s") == 3600
    # explicit override beats env
    op_config.set("daily_cooldown_s", 60)
    assert op_config.get("daily_cooldown_s") == 60


def test_set_clamps_to_floor_and_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("NDRCHST_OP_CONFIG", str(tmp_path / "c.json"))
    op_config._reset_for_tests()
    assert op_config.set("daily_cooldown_s", -5) == 0   # floored
    # a fresh load from the same file sees the persisted value
    op_config._reset_for_tests()
    op_config._load()
    assert op_config.get("daily_cooldown_s") == 0


def test_set_unknown_knob_raises():
    import pytest
    with pytest.raises(KeyError):
        op_config.set("nope", 1)


def test_all_values_covers_every_knob():
    vals = op_config.all_values()
    assert set(vals) == set(op_config.KNOBS)


def test_daily_cooldown_knob_changes_claim(tmp_path):
    """A short cooldown lets a second claim through; the knob is the lever."""
    db = tmp_path / "t.db"
    conn = connect(db)
    try:
        ok, _ = dc.try_claim(conn, "W", cooldown_s=op_config.get("daily_cooldown_s"))
        assert ok
        # still on the 24h default
        ok2, left = dc.try_claim(conn, "W", cooldown_s=op_config.get("daily_cooldown_s"))
        assert not ok2 and left > 0
        # operator drops the cooldown to 0 → immediately claimable again
        op_config.set("daily_cooldown_s", 0)
        ok3, _ = dc.try_claim(conn, "W", cooldown_s=op_config.get("daily_cooldown_s"))
        assert ok3
    finally:
        conn.close()
