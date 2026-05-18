"""Realistic server-directory builder for end-to-end tests.

Lays down a tree that mirrors what a real Paper 1.21.x server's data dir
looks like after first boot:

    <root>/
      server.jar
      eula.txt
      server.properties
      ops.json, whitelist.json, banned-players.json, banned-ips.json
      plugins/
        SomePlugin.jar
      mods/
        SomeMod.jar
      world/
        level.dat            (gzipped NBT — see make_level_dat)
        session.lock
        region/
          r.0.0.mca         (small placeholder; not real Anvil format)

The NBT is real and parseable by our domain/worlds.py module.
"""
from __future__ import annotations

import json
import secrets
from pathlib import Path

from nbtlib import File
from nbtlib.tag import Byte, Compound, Int, Long, String


def make_level_dat(
    *,
    level_name: str = "Survival World",
    seed: int = 7331,
    spawn: tuple[int, int, int] = (0, 64, 0),
    time: int = 12000,
    day_time: int = 6000,
    raining: bool = False,
    thundering: bool = False,
    version: int = 19133,
    game_rules: dict[str, str] | None = None,
    use_modern_seed: bool = True,
) -> bytes:
    """Build a gzipped NBT level.dat-shaped byte string."""
    if game_rules is None:
        game_rules = {
            "doDaylightCycle": "true",
            "doMobSpawning": "true",
            "keepInventory": "false",
            "mobGriefing": "true",
        }
    data = Compound({
        "LevelName": String(level_name),
        "SpawnX": Int(spawn[0]),
        "SpawnY": Int(spawn[1]),
        "SpawnZ": Int(spawn[2]),
        "Time": Long(time),
        "DayTime": Long(day_time),
        "raining": Byte(1 if raining else 0),
        "thundering": Byte(1 if thundering else 0),
        "version": Int(version),
        "GameRules": Compound({k: String(v) for k, v in game_rules.items()}),
    })
    if use_modern_seed:
        data["WorldGenSettings"] = Compound({"seed": Long(seed)})
    else:
        data["RandomSeed"] = Long(seed)

    f = File({"": Compound({"Data": data})}, gzipped=True)
    import io
    buf = io.BytesIO()
    f.save(buf, gzipped=True)
    return buf.getvalue()


def seed_server_dir(root: Path, *, world_name: str = "world") -> Path:
    """Build a realistic Paper-shaped server data dir at `root`."""
    root.mkdir(parents=True, exist_ok=True)

    (root / "server.jar").write_bytes(b"PK\x03\x04fake-paper-jar")
    (root / "eula.txt").write_text("eula=true\n")
    (root / "server.properties").write_text(
        "motd=A Minecraft Server\n"
        f"level-name={world_name}\n"
        "server-port=25565\n"
        "max-players=20\n"
        "online-mode=true\n"
        "enable-rcon=false\n"
    )
    (root / "ops.json").write_text(json.dumps([
        {"uuid": str(secrets.token_hex(16)), "name": "AdminOne", "level": 4}
    ]))
    (root / "whitelist.json").write_text("[]")
    (root / "banned-players.json").write_text("[]")
    (root / "banned-ips.json").write_text("[]")

    plugins = root / "plugins"
    plugins.mkdir()
    (plugins / "EssentialsX.jar").write_bytes(b"PK\x03\x04plugin")
    (plugins / "DiscordSRV.jar").write_bytes(b"PK\x03\x04plugin2")

    mods = root / "mods"
    mods.mkdir()
    (mods / "fabric-api.jar").write_bytes(b"PK\x03\x04mod")

    world = root / world_name
    world.mkdir()
    (world / "level.dat").write_bytes(make_level_dat(level_name=world_name.title()))
    (world / "session.lock").write_bytes(b"\x00" * 8)
    region = world / "region"
    region.mkdir()
    (region / "r.0.0.mca").write_bytes(b"\x00" * 4096)  # placeholder
    (world / "datapacks").mkdir()

    return root
