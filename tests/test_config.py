"""Config domain tests (pure logic, no Docker)."""
from __future__ import annotations

import pytest

from ndrchst.domain import config as cfg


def test_validate_memory_accepts_normal_values():
    assert cfg.validate_memory(2048) == 2048
    assert cfg.validate_memory(512) == 512


def test_validate_memory_rejects_below_floor():
    with pytest.raises(cfg.ConfigError, match=">= 512"):
        cfg.validate_memory(100)


def test_validate_memory_rejects_above_ceiling():
    with pytest.raises(cfg.ConfigError, match="<="):
        cfg.validate_memory(1024 * 1024)


def test_normalize_jvm_flags_passes_aikar_style():
    raw = "-XX:+UseG1GC -XX:+ParallelRefProcEnabled -XX:MaxGCPauseMillis=200"
    assert cfg.normalize_jvm_flags(raw) == raw


def test_normalize_jvm_flags_strips_and_empties():
    assert cfg.normalize_jvm_flags("   ") is None
    assert cfg.normalize_jvm_flags("") is None
    assert cfg.normalize_jvm_flags(None) is None


def test_normalize_jvm_flags_rejects_xmx_collision():
    with pytest.raises(cfg.ConfigError, match="conflicts"):
        cfg.normalize_jvm_flags("-Xmx4G -XX:+UseG1GC")


def test_normalize_jvm_flags_rejects_jar_override():
    with pytest.raises(cfg.ConfigError, match="conflicts"):
        cfg.normalize_jvm_flags("-jar evil.jar")


def test_normalize_jvm_flags_rejects_unbalanced_quotes():
    with pytest.raises(cfg.ConfigError, match="don't parse"):
        cfg.normalize_jvm_flags('-Dfoo="bar')


def test_parse_env_vars_basic():
    raw = "FOO=bar\nBAZ=qux"
    assert cfg.parse_env_vars(raw) == {"FOO": "bar", "BAZ": "qux"}


def test_parse_env_vars_strips_blanks_and_comments():
    raw = "# heading\n\nFOO=1\n# another\nBAR=2\n"
    assert cfg.parse_env_vars(raw) == {"FOO": "1", "BAR": "2"}


def test_parse_env_vars_rejects_reserved():
    with pytest.raises(cfg.ConfigError, match="EULA"):
        cfg.parse_env_vars("EULA=FALSE")
    with pytest.raises(cfg.ConfigError, match="LD_LIBRARY_PATH"):
        cfg.parse_env_vars("LD_LIBRARY_PATH=/evil")


def test_parse_env_vars_rejects_bad_keys():
    with pytest.raises(cfg.ConfigError, match="valid env var name"):
        cfg.parse_env_vars("123BAD=foo")
    with pytest.raises(cfg.ConfigError, match="valid env var name"):
        cfg.parse_env_vars("HAS SPACE=foo")


def test_parse_env_vars_rejects_missing_equals():
    with pytest.raises(cfg.ConfigError, match="KEY=VALUE"):
        cfg.parse_env_vars("JUST_A_KEY")


def test_parse_env_vars_allows_equals_in_value():
    """Values can contain = (it's only the *first* = that's the separator)."""
    assert cfg.parse_env_vars("JAVA_OPTS=-Dx=y") == {"JAVA_OPTS": "-Dx=y"}


def test_normalize_env_vars_roundtrips_and_canonicalises():
    raw = "# comment\nFOO=1\n\nBAR=2\n"
    out = cfg.normalize_env_vars(raw)
    assert out == "FOO=1\nBAR=2"


def test_normalize_env_vars_empty_to_none():
    assert cfg.normalize_env_vars("") is None
    assert cfg.normalize_env_vars(None) is None
    assert cfg.normalize_env_vars("   \n# only a comment\n") is None
