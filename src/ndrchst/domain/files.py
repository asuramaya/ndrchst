"""Per-server file browser scoped to data_dir. Path-traversal safe.

Public functions take a `relative` string and resolve it strictly inside
`data_dir`. Resolution is via Path.resolve() comparison against the
data_dir's canonical path — symlinks pointing outside are rejected too.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Editable file types — names + extensions we'll let the UI open in-place.
_EDITABLE_NAMES = {
    "server.properties", "eula.txt", "ops.json", "whitelist.json",
    "banned-players.json", "banned-ips.json", "spigot.yml", "bukkit.yml",
    "paper-global.yml", "paper-world-defaults.yml",
    "allowlist.json", "permissions.json", "valid_known_packs.json",
}
_EDITABLE_EXT = {".yml", ".yaml", ".json", ".properties", ".txt", ".toml", ".conf"}

# Hard cap on what we'll load into the editor textarea (don't OOM the UI)
MAX_EDIT_BYTES = 256 * 1024  # 256 KB


class PathError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Entry:
    name: str
    path: str       # relative to data_dir, forward slashes
    is_dir: bool
    size: int
    editable: bool

    @property
    def size_human(self) -> str:
        if self.is_dir:
            return ""
        n = self.size
        for unit in ("B", "K", "M", "G", "T"):
            if n < 1024:
                return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
            n /= 1024
        return f"{n:.1f}P"


def _resolve(data_dir: Path, relative: str) -> Path:
    base = data_dir.resolve()
    # treat empty as the root; strip leading slash + normalize
    rel = (relative or "").lstrip("/").replace("\\", "/")
    candidate = (base / rel).resolve()
    if base != candidate and base not in candidate.parents:
        raise PathError(f"refusing path outside data_dir: {relative!r}")
    return candidate


def _editable(name: str) -> bool:
    if name in _EDITABLE_NAMES:
        return True
    return Path(name).suffix.lower() in _EDITABLE_EXT


def list_dir(data_dir: Path, relative: str = "") -> list[Entry]:
    target = _resolve(data_dir, relative)
    if not target.exists() or not target.is_dir():
        raise PathError(f"not a directory: {relative!r}")
    base = data_dir.resolve()
    entries: list[Entry] = []
    for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        rel = child.resolve().relative_to(base).as_posix()
        is_dir = child.is_dir()
        size = 0 if is_dir else child.stat().st_size
        entries.append(Entry(
            name=child.name, path=rel, is_dir=is_dir,
            size=size, editable=(not is_dir) and _editable(child.name),
        ))
    return entries


def read_text(data_dir: Path, relative: str) -> str:
    target = _resolve(data_dir, relative)
    if not target.exists() or not target.is_file():
        raise PathError(f"not a file: {relative!r}")
    if target.stat().st_size > MAX_EDIT_BYTES:
        raise PathError(f"file too large to edit ({target.stat().st_size} > {MAX_EDIT_BYTES} bytes)")
    return target.read_text(encoding="utf-8", errors="replace")


def write_text(data_dir: Path, relative: str, content: str) -> None:
    target = _resolve(data_dir, relative)
    if target.is_dir():
        raise PathError(f"is a directory: {relative!r}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
