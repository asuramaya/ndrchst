"""Per-server plugin inventory + lifecycle for Java servers.

A "plugin" here means a Bukkit/Spigot/Paper .jar in <data_dir>/plugins/.
Native mods (Forge/Fabric) live in mods/ instead; the marketplace tab
covers those via Modrinth. This module is purely filesystem + jar inspection:
  * list_plugins  — scan plugins/*.jar and *.jar.disabled, parse plugin.yml
  * toggle        — rename .jar ↔ .jar.disabled
  * remove        — delete the jar
  * save_upload   — accept an uploaded jar safely

Disabling a plugin by renaming its file to .jar.disabled is the Paper-blessed
convention; the server skips files that don't end in .jar at load time.
"""
from __future__ import annotations

import hashlib
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import IO


class PluginError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class PluginInfo:
    """One discovered plugin jar."""
    filename: str        # e.g. "Geyser-Spigot.jar" (or ".jar.disabled")
    name: str | None     # from plugin.yml "name:"
    version: str | None  # from plugin.yml "version:"
    author: str | None   # from plugin.yml "author:" or first of "authors:"
    api_version: str | None
    disabled: bool
    size: int

    @property
    def display_name(self) -> str:
        return self.name or self.filename.removesuffix(".disabled").removesuffix(".jar")


def list_plugins(data_dir: Path) -> list[PluginInfo]:
    """List every plugin jar (enabled and disabled) in plugins/. Returns
    empty list if the dir doesn't exist (server hasn't booted yet)."""
    plugins_dir = data_dir / "plugins"
    if not plugins_dir.exists() or not plugins_dir.is_dir():
        return []
    found: list[PluginInfo] = []
    for path in sorted(plugins_dir.iterdir()):
        if not path.is_file():
            continue
        name = path.name
        if not (name.endswith(".jar") or name.endswith(".jar.disabled")):
            continue
        disabled = name.endswith(".jar.disabled")
        info = _parse_plugin_yml(path)
        found.append(PluginInfo(
            filename=name,
            name=info.get("name"),
            version=info.get("version"),
            author=info.get("author"),
            api_version=info.get("api_version"),
            disabled=disabled,
            size=path.stat().st_size,
        ))
    return found


def toggle_plugin(data_dir: Path, filename: str) -> str:
    """Toggle enabled/disabled by renaming. Returns the new filename."""
    src = (data_dir / "plugins" / filename).resolve()
    base = (data_dir / "plugins").resolve()
    if base not in src.parents and src != base:
        raise PluginError(f"plugin path escapes plugins/: {filename}")
    if not src.exists():
        raise PluginError(f"plugin not found: {filename}")
    if filename.endswith(".jar"):
        dst = src.with_name(filename + ".disabled")
    elif filename.endswith(".jar.disabled"):
        dst = src.with_name(filename.removesuffix(".disabled"))
    else:
        raise PluginError(f"not a plugin jar: {filename}")
    src.rename(dst)
    return dst.name


def remove_plugin(data_dir: Path, filename: str) -> None:
    """Delete a plugin jar. Caller already confirmed with the user."""
    src = (data_dir / "plugins" / filename).resolve()
    base = (data_dir / "plugins").resolve()
    if base not in src.parents and src != base:
        raise PluginError(f"plugin path escapes plugins/: {filename}")
    if not src.exists():
        raise PluginError(f"plugin not found: {filename}")
    src.unlink()


def sha1_of(path: Path) -> str:
    """Stream-hash a file with SHA1. Modrinth's version_files endpoint indexes
    by SHA1; we never need MD5 or SHA512 here."""
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_inventory(data_dir: Path) -> dict[str, str]:
    """For every enabled jar in plugins/, compute the SHA1 once. Returns
    filename -> hash. Disabled jars are excluded; you can't run an update
    on something that isn't loaded."""
    plugins_dir = data_dir / "plugins"
    if not plugins_dir.exists():
        return {}
    return {
        p.name: sha1_of(p)
        for p in sorted(plugins_dir.iterdir())
        if p.is_file() and p.name.endswith(".jar")
    }


def replace_plugin(
    data_dir: Path, filename: str, new_bytes: bytes, *, new_filename: str | None = None,
) -> Path:
    """Atomically swap a plugin's contents for a new build. The old jar is
    removed; the new bytes are written to `<new_filename or filename>`. The
    operation goes through a `.upload` temp like save_upload so a half-write
    can't get loaded by the server."""
    src = (data_dir / "plugins" / filename).resolve()
    base = (data_dir / "plugins").resolve()
    if base not in src.parents and src != base:
        raise PluginError(f"plugin path escapes plugins/: {filename}")
    if not src.exists():
        raise PluginError(f"plugin not found: {filename}")
    target_name = new_filename or filename
    if "/" in target_name or "\\" in target_name or target_name.startswith("."):
        raise PluginError(f"unsafe filename: {target_name}")
    if not target_name.endswith(".jar"):
        raise PluginError("new filename must end in .jar")
    target = base / target_name
    tmp = target.with_suffix(target.suffix + ".upload")
    tmp.write_bytes(new_bytes)
    if not zipfile.is_zipfile(tmp):
        tmp.unlink(missing_ok=True)
        raise PluginError("downloaded file is not a valid jar")
    src.unlink()
    tmp.rename(target)
    return target


def save_upload(data_dir: Path, filename: str, stream: IO[bytes]) -> Path:
    """Save an uploaded jar. Sanitises the filename + verifies it's a
    valid zip (a non-trivial jar at minimum) before commit."""
    # Sanitise filename
    if not filename.endswith(".jar"):
        raise PluginError("filename must end in .jar")
    safe = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "/" in safe or "\\" in safe or safe.startswith("."):
        raise PluginError(f"unsafe filename: {filename}")

    plugins_dir = data_dir / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    target = plugins_dir / safe

    # Write to a temp first so a half-written jar can't get picked up
    tmp = target.with_suffix(target.suffix + ".upload")
    with tmp.open("wb") as f:
        shutil.copyfileobj(stream, f)

    if not zipfile.is_zipfile(tmp):
        tmp.unlink(missing_ok=True)
        raise PluginError("uploaded file is not a valid jar (zip)")

    tmp.rename(target)
    return target


# ─── internals ─────────────────────────────────────────────────────────────


def _parse_plugin_yml(jar: Path) -> dict:
    """Extract a few fields from plugin.yml inside the jar. Failures are
    silent — we just return an empty dict and the caller falls back to the
    filename. Some jars don't have plugin.yml (paper-plugin.yml is the
    newer flavour) so we try both."""
    fields: dict[str, str] = {}
    try:
        with zipfile.ZipFile(jar) as zf:
            for name in ("plugin.yml", "paper-plugin.yml"):
                if name in zf.namelist():
                    text = zf.read(name).decode("utf-8", errors="replace")
                    fields.update(_yaml_scan(text))
                    break
    except (zipfile.BadZipFile, OSError):
        return {}
    return fields


def _yaml_scan(text: str) -> dict:
    """Minimal top-level key=value scanner — we only want name/version/
    author/api-version. Plugin.yml is dead simple; a real YAML lib is
    overkill and would add a dependency. If a value is wrapped in quotes
    we strip them."""
    out: dict[str, str] = {}
    wanted = {"name", "version", "author", "main", "api-version"}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line or line.startswith(" ") or line.startswith("\t"):
            continue  # ignore indented values (nested mappings, lists)
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not value:
            continue
        if key in wanted:
            out[key.replace("-", "_")] = value
        elif key == "authors" and value.startswith("[") and value.endswith("]"):
            # Inline-list form: `authors: [Alice, Bob]`
            first = value[1:-1].split(",")[0].strip().strip('"').strip("'")
            out["author"] = first
    return out
