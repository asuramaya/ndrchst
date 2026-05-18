"""World metadata reader/writer for Java Edition.

Java stores level metadata in `<world>/level.dat`, a gzipped NBT file with
the schema:

    ""                                     # named root (unnamed in old data)
      Data
        LevelName        TAG_String        # display name
        RandomSeed       TAG_Long          # legacy; modern uses WorldGenSettings.seed
        WorldGenSettings (Compound)        # 1.16+; contains seed + dimensions
          seed           TAG_Long
        SpawnX/Y/Z       TAG_Int
        Time             TAG_Long          # game time (always-increasing ticks)
        DayTime          TAG_Long          # 0-23999 wall clock
        raining          TAG_Byte
        thundering       TAG_Byte
        version          TAG_Int           # data version (NBT layout version)
        GameRules        (Compound[String])  # key=value where value is a stringly-typed bool/int

Bedrock uses LevelDB and a different metadata file; out of scope for v0.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nbtlib import File
from nbtlib.tag import Byte, Compound, Int, Long, String


@dataclass(frozen=True, slots=True)
class WorldInfo:
    name: str
    seed: int
    spawn: tuple[int, int, int]
    time: int          # always-increasing ticks
    day_time: int      # 0-23999 within a day
    raining: bool
    thundering: bool
    version: int
    game_rules: dict[str, str]


class WorldError(Exception):
    pass


def _world_root(data_dir: Path) -> Path:
    """Find the world folder. Defaults to 'world'; falls back to whatever
    level-name in server.properties points at, or the first dir with a
    level.dat inside data_dir."""
    candidate = data_dir / "world"
    if (candidate / "level.dat").exists():
        return candidate
    # try server.properties
    props = data_dir / "server.properties"
    if props.exists():
        for line in props.read_text().splitlines():
            if line.startswith("level-name="):
                name = line.split("=", 1)[1].strip()
                p = data_dir / name
                if (p / "level.dat").exists():
                    return p
                break
    # last resort: scan
    for child in data_dir.iterdir():
        if child.is_dir() and (child / "level.dat").exists():
            return child
    raise WorldError(f"no level.dat found under {data_dir}")


def _extract_seed(data: Compound) -> int:
    # Modern (1.16+): WorldGenSettings.seed
    wgs = data.get("WorldGenSettings")
    if wgs is not None and "seed" in wgs:
        return int(wgs["seed"])
    # Legacy
    if "RandomSeed" in data:
        return int(data["RandomSeed"])
    return 0


def read(data_dir: Path) -> WorldInfo:
    root_dir = _world_root(data_dir)
    f = File.load(root_dir / "level.dat", gzipped=True)
    # The root key may be unnamed ('') for new worlds; nbtlib presents it as f[''] either way
    root = f.get("", f)  # if there's an unnamed root, use it; else f itself is the root
    if not isinstance(root, Compound) or "Data" not in root:
        raise WorldError(f"unexpected NBT structure in {root_dir / 'level.dat'}")
    data = root["Data"]
    return WorldInfo(
        name=str(data.get("LevelName", String(""))),
        seed=_extract_seed(data),
        spawn=(int(data.get("SpawnX", Int(0))), int(data.get("SpawnY", Int(0))), int(data.get("SpawnZ", Int(0)))),
        time=int(data.get("Time", Long(0))),
        day_time=int(data.get("DayTime", Long(0))),
        raining=bool(int(data.get("raining", Byte(0)))),
        thundering=bool(int(data.get("thundering", Byte(0)))),
        version=int(data.get("version", Int(0))),
        game_rules={k: str(v) for k, v in (data.get("GameRules") or {}).items()},
    )


def write_game_rules(data_dir: Path, updates: dict[str, str]) -> None:
    """Mutate game rules in level.dat. Java accepts new rules at world load
    so this requires a restart to take effect."""
    root_dir = _world_root(data_dir)
    target = root_dir / "level.dat"
    f = File.load(target, gzipped=True)
    root = f.get("", f)
    data = root["Data"]
    existing = data.get("GameRules") or Compound()
    for k, v in updates.items():
        existing[k] = String(str(v))
    data["GameRules"] = existing
    # nbtlib's File.save preserves gzipping by default if loaded gzipped
    f.save(target, gzipped=True)
