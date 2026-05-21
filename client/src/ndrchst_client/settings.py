"""Runtime config loading — decouples the launcher binary from per-server
config so one cross-platform build serves any server.

Resolution order (first hit wins):
  1. $NDRCHST_CLIENT_CONFIG — path to a JSON config file.
  2. A `client-config.json` sitting next to the executable.
  3. The remembered pin at ~/.ndrchst-client/client-config.json — written by a
     ndrchst:// deep link (deeplink.fetch_server_config). This is what lets the
     standalone exe AUTODETECT the server it was linked to on later launches,
     instead of forgetting it and forcing another browser round-trip to Play.
     Only consulted for a GENERIC build (no baked server_id); a per-server .zip
     is already pinned and ignores it so a stray pin can't hijack it.
  4. A `client-config.json` in the cwd.
  5. The baked-in `config.py` defaults (back-compat with the old per-server
     source bundles).

A config JSON mirrors the config.py field names (lowercase ok too):
  {
    "app_name": "...", "server_host": "...", "server_port": 25590,
    "mc_version": "1.21.1", "neoforge_version": "21.1.228",
    "modpack_url": "...", "mods_sync_url": "...",
    "tunnel_hostname": "mc.ndrchst.com", "default_username": "Player",
    "server_id": "..."
  }
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from . import config as _baked


@dataclass(frozen=True, slots=True)
class ClientConfig:
    app_name: str
    server_host: str
    server_port: int
    mc_version: str
    default_username: str
    server_id: str
    neoforge_version: str | None = None
    modpack_url: str | None = None
    mods_sync_url: str | None = None
    tunnel_hostname: str | None = None
    update_base_url: str | None = None
    auth_base_url: str | None = None  # site base for wallet sign-in (/client/auth/*)


def _exe_dir() -> Path:
    """Directory of the running executable (PyInstaller) or cwd."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def _from_baked() -> dict:
    return {
        "app_name": getattr(_baked, "APP_NAME", "ndrchst Client"),
        "server_host": getattr(_baked, "SERVER_HOST", ""),
        "server_port": getattr(_baked, "SERVER_PORT", 25565),
        "mc_version": getattr(_baked, "MC_VERSION", "1.21.1"),
        "default_username": getattr(_baked, "DEFAULT_USERNAME", "Player"),
        "server_id": getattr(_baked, "SERVER_ID", ""),
        "neoforge_version": getattr(_baked, "NEOFORGE_VERSION", None),
        "modpack_url": getattr(_baked, "MODPACK_URL", None),
        "mods_sync_url": getattr(_baked, "MODS_SYNC_URL", None),
        "tunnel_hostname": getattr(_baked, "TUNNEL_HOSTNAME", None),
        "update_base_url": getattr(_baked, "UPDATE_BASE_URL", None),
        "auth_base_url": getattr(_baked, "AUTH_BASE_URL", None),
    }


# Where a deep link remembers the pinned server (mirrors deeplink._DATA_DIR).
_REMEMBERED_CONFIG = Path.home() / ".ndrchst-client" / "client-config.json"


def _candidate_paths(*, generic: bool) -> list[Path]:
    paths: list[Path] = []
    env = os.environ.get("NDRCHST_CLIENT_CONFIG")
    if env:
        paths.append(Path(env))
    paths.append(_exe_dir() / "client-config.json")
    # A generic build remembers the server a deep link pinned it to so it
    # autodetects on relaunch — no browser round-trip needed once authenticated.
    if generic:
        paths.append(_REMEMBERED_CONFIG)
    paths.append(Path.cwd() / "client-config.json")
    return paths


def load() -> ClientConfig:
    """Merge baked defaults with the first JSON config found (if any)."""
    merged = _from_baked()
    generic = not merged.get("server_id")
    for p in _candidate_paths(generic=generic):
        if p.is_file():
            try:
                data = json.loads(p.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            # Accept both lowercase and UPPERCASE keys.
            for k, v in data.items():
                merged[k.lower()] = v
            break
    return ClientConfig(**{k: merged.get(k) for k in ClientConfig.__dataclass_fields__})
