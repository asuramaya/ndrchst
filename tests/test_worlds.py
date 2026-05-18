"""Worlds NBT parsing tests against generated level.dat files."""
from __future__ import annotations

from pathlib import Path

import pytest

from ndrchst.domain import worlds
from tests.scenario import make_level_dat, seed_server_dir


def test_read_round_trips_modern_seed(tmp_path: Path):
    root = seed_server_dir(tmp_path)
    info = worlds.read(root)
    assert info.name == "World"  # title-cased from "world"
    assert info.seed == 7331
    assert info.spawn == (0, 64, 0)
    assert info.day_time == 6000
    assert info.time == 12000
    assert info.raining is False
    assert info.game_rules["keepInventory"] == "false"


def test_read_legacy_random_seed(tmp_path: Path):
    world = tmp_path / "world"
    world.mkdir()
    (world / "level.dat").write_bytes(make_level_dat(seed=42, use_modern_seed=False))
    info = worlds.read(tmp_path)
    assert info.seed == 42


def test_read_uses_level_name_from_server_properties(tmp_path: Path):
    (tmp_path / "server.properties").write_text("level-name=funkyworld\n")
    funky = tmp_path / "funkyworld"
    funky.mkdir()
    (funky / "level.dat").write_bytes(make_level_dat(level_name="Funky"))
    info = worlds.read(tmp_path)
    assert info.name == "Funky"


def test_read_falls_back_to_first_world_dir(tmp_path: Path):
    weirdname = tmp_path / "mc_world_2024"
    weirdname.mkdir()
    (weirdname / "level.dat").write_bytes(make_level_dat(level_name="WeirdName"))
    info = worlds.read(tmp_path)
    assert info.name == "WeirdName"


def test_read_no_world_raises(tmp_path: Path):
    with pytest.raises(worlds.WorldError, match=r"no level\.dat"):
        worlds.read(tmp_path)


def test_weather_flags(tmp_path: Path):
    world = tmp_path / "world"
    world.mkdir()
    (world / "level.dat").write_bytes(
        make_level_dat(raining=True, thundering=True)
    )
    info = worlds.read(tmp_path)
    assert info.raining and info.thundering


def test_write_game_rules_round_trip(tmp_path: Path):
    seed_server_dir(tmp_path)
    worlds.write_game_rules(tmp_path, {"keepInventory": "true", "newRule": "42"})
    info = worlds.read(tmp_path)
    assert info.game_rules["keepInventory"] == "true"
    assert info.game_rules["newRule"] == "42"
    # untouched rule preserved
    assert info.game_rules["doDaylightCycle"] == "true"


def test_write_preserves_unrelated_data(tmp_path: Path):
    seed_server_dir(tmp_path)
    before = worlds.read(tmp_path)
    worlds.write_game_rules(tmp_path, {"keepInventory": "true"})
    after = worlds.read(tmp_path)
    assert before.seed == after.seed
    assert before.spawn == after.spawn
    assert before.day_time == after.day_time
    assert before.name == after.name


def test_extreme_seed_value_round_trips(tmp_path: Path):
    """64-bit seeds matter; ensure no precision loss."""
    extreme = -9223372036854775808  # Java Long.MIN_VALUE
    world = tmp_path / "world"
    world.mkdir()
    (world / "level.dat").write_bytes(make_level_dat(seed=extreme))
    info = worlds.read(tmp_path)
    assert info.seed == extreme
