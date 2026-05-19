"""Per-server pilot client bundle generation.

When a Java server is created, ndrchst writes a "pilot bundle" pinned to that
server's host + port + Minecraft version. The bundle is a zip containing:

  ndrchst_pilot/    — the pilot-client source tree, with a generated config.py
                      that hard-codes this server's coordinates
  requirements.txt  — `portablemc>=4.4` (the only external dep)
  launch.sh / .bat  — convenience launchers
  README.txt        — instructions

End users grab the zip from the public surface and run:
    unzip pilot.zip && cd pilot
    pip install -r requirements.txt
    python -m ndrchst_pilot

Bundles are regenerated whenever the source pilot-client/ tree changes;
they're cheap (a few hundred KB, no compile step). Real native binaries
would need PyInstaller per-OS; that's a future GH Actions job — the
contract (zip layout + module entry-point) stays the same.

Layout on disk:
    ~/.ndrchst/pilots/<server_id>/
        pilot.zip
        config.json              # raw machine-readable
        manifest.json            # build metadata (ndrchst version, mtime, sha256)
"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..domain.models import Family, Server

PILOTS_ROOT_DEFAULT = Path.home() / ".ndrchst" / "pilots"
PILOT_SOURCE_DIR = Path(__file__).resolve().parents[3] / "pilot-client"


class PilotBuildError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class PilotBundle:
    """Result of generating a pilot bundle for a server."""
    server_id: str
    zip_path: Path
    config_path: Path
    manifest_path: Path
    size: int
    sha256: str


def _looks_like_mc_version(s: str) -> bool:
    """Heuristic: does this look like a vanilla MC version (e.g. 1.21.1)
    vs a URL or arbitrary version string used by modpack-type servers?"""
    if not s or "/" in s or ":" in s:
        return False
    parts = s.split(".")
    return all(p.isdigit() for p in parts) and 2 <= len(parts) <= 4


def _public_host(server: Server, *, public_host: str) -> str:
    """Pick the host the pilot should connect to. Defaults to `public_host`
    (set per-deployment via env or AppState). Falls back to a sentinel that
    end users will need to edit manually."""
    return public_host or "REPLACE_WITH_SERVER_HOST"


def build_bundle(
    server: Server,
    *,
    public_host: str,
    edge_url: str = "",
    pilots_root: Path | None = None,
    source_dir: Path | None = None,
    tunnel_hostname: str = "",
    modpack_url: str = "",
    neoforge_version: str = "",
) -> PilotBundle:
    """Generate and persist a pilot zip for this server. Idempotent — running
    twice overwrites with a fresh build.

    `public_host` is the address MC clients will dial for game traffic
    (e.g. "mc.ndrchst.com"). `edge_url` is the HTTP base URL where the
    pilot zip + manifest live publicly (e.g. "https://play.ndrchst.com")
    — surfaced in the README so end-users know where to grab updates.

    Only Java servers get a pilot. Bedrock servers raise PilotBuildError;
    callers should guard.
    """
    if server.family is not Family.JAVA:
        raise PilotBuildError(
            f"pilot is Java-only; server '{server.name}' is {server.family.value}"
        )
    pilots_root = pilots_root or PILOTS_ROOT_DEFAULT
    source_dir = source_dir or PILOT_SOURCE_DIR
    if not source_dir.exists():
        raise PilotBuildError(f"pilot source tree missing at {source_dir}")

    out_dir = pilots_root / server.id
    out_dir.mkdir(parents=True, exist_ok=True)

    host = _public_host(server, public_host=public_host)
    # MC clients need a real semver MC version, not a URL (which is what
    # modpack-platform servers store). Resolve modded servers to the MC
    # version that the modpack targets — passed in via neoforge_version /
    # mc_version if known; otherwise fall back to 1.21.1 (current ATM10
    # generation) as a sane default until we plumb pack-specific data.
    mc_version = server.version
    if not _looks_like_mc_version(mc_version):
        mc_version = "1.21.1"
    # Mods sync URL: server is source of truth, pilot pulls its mod set from
    # this endpoint at install time. Defaults to the edge-served path; can
    # be overridden by the caller for testing or air-gapped setups.
    mods_sync_url = (
        f"{edge_url.rstrip('/')}/pilot/{server.id}" if edge_url else None
    )
    config = {
        "app_name": f"ndrchst Pilot — {server.name}",
        "server_host": host,
        "server_port": server.port,
        "mc_version": mc_version,
        "default_username": "Player",
        "server_id": server.id,
        "edge_url": edge_url or "",
        "tunnel_hostname": tunnel_hostname or None,
        "modpack_url": modpack_url or None,
        "mods_sync_url": mods_sync_url,
        "neoforge_version": neoforge_version or None,
    }

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # 1. Generated config.py overrides the source's defaults.
        generated_config = _render_config_py(config)
        zf.writestr("ndrchst_pilot/config.py", generated_config)

        # 2. Vendor the pilot-client source (everything except its own config.py).
        src = source_dir / "src" / "ndrchst_pilot"
        for path in sorted(src.rglob("*.py")):
            if path.name == "config.py":
                continue  # we wrote our own above
            arc = "ndrchst_pilot/" + str(path.relative_to(src))
            zf.writestr(arc, path.read_text())

        # 3. requirements.txt — portablemc for the launcher core, httpx
        # for the curseforge resolver (used by modpack install path).
        zf.writestr("requirements.txt", "portablemc>=4.4\nhttpx>=0.27\n")

        # 4. launchers
        zf.writestr("launch.sh", _LAUNCH_SH)
        zf.writestr("launch.bat", _LAUNCH_BAT)

        # 5. README
        zf.writestr("README.txt", _readme(config))

    zip_bytes = zip_buf.getvalue()
    zip_path = out_dir / "pilot.zip"
    zip_path.write_bytes(zip_bytes)

    config_path = out_dir / "config.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n")

    sha = hashlib.sha256(zip_bytes).hexdigest()
    manifest = {
        "server_id": server.id,
        "server_name": server.name,
        "mc_version": server.version,
        "host": host,
        "port": server.port,
        "edge_url": edge_url or "",
        "built_at": datetime.now(UTC).isoformat(),
        "size": len(zip_bytes),
        "sha256": sha,
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    return PilotBundle(
        server_id=server.id,
        zip_path=zip_path,
        config_path=config_path,
        manifest_path=manifest_path,
        size=len(zip_bytes),
        sha256=sha,
    )


def remove_bundle(server_id: str, *, pilots_root: Path | None = None) -> None:
    """Drop a server's pilot directory. Called when the server is deleted."""
    pilots_root = pilots_root or PILOTS_ROOT_DEFAULT
    target = pilots_root / server_id
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)


def bundle_path(server_id: str, *, pilots_root: Path | None = None) -> Path | None:
    """Where the bundle for this server lives on disk, or None if missing."""
    pilots_root = pilots_root or PILOTS_ROOT_DEFAULT
    p = pilots_root / server_id / "pilot.zip"
    return p if p.exists() else None


def _render_config_py(cfg: dict) -> str:
    """Generate a Python module mirroring pilot-client/src/ndrchst_pilot/config.py
    but with this server's coordinates baked in."""
    return (
        '"""Per-server build-time config. Generated by ndrchst — do not edit."""\n'
        f"APP_NAME = {cfg['app_name']!r}\n"
        f"SERVER_HOST = {cfg['server_host']!r}\n"
        f"SERVER_PORT = {cfg['server_port']!r}\n"
        f"MC_VERSION = {cfg['mc_version']!r}\n"
        f"NEOFORGE_VERSION = {cfg.get('neoforge_version')!r}\n"
        f"MODPACK_URL = {cfg.get('modpack_url')!r}\n"
        f"MODS_SYNC_URL = {cfg.get('mods_sync_url')!r}\n"
        f"TUNNEL_HOSTNAME = {cfg.get('tunnel_hostname')!r}\n"
        f"DEFAULT_USERNAME = {cfg['default_username']!r}\n"
        f"SERVER_ID = {cfg['server_id']!r}\n"
    )


_LAUNCH_SH = """\
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
exec .venv/bin/python -m ndrchst_pilot
"""

_LAUNCH_BAT = """\
@echo off
cd /d %~dp0
python -m venv .venv
.venv\\Scripts\\pip install -q -r requirements.txt
.venv\\Scripts\\python -m ndrchst_pilot
"""


def _readme(cfg: dict) -> str:
    edge_line = ""
    if cfg.get("edge_url"):
        edge_line = (
            f"\nGrab the latest copy of this bundle any time at:\n"
            f"  {cfg['edge_url'].rstrip('/')}/pilot/{cfg['server_id']}/pilot.zip\n"
        )
    return f"""\
{cfg['app_name']}
{'=' * len(cfg['app_name'])}

This bundle is pinned to Minecraft {cfg['mc_version']} connecting to
{cfg['server_host']}:{cfg['server_port']}.

Quick start:
  unzip pilot.zip && cd pilot
  ./launch.sh             (Linux / Mac)
  launch.bat              (Windows)

What this does:
  1. Creates a local Python venv (./.venv)
  2. Installs `portablemc` (the only dependency)
  3. Launches the Minecraft offline-mode pilot GUI
  4. Connects you to {cfg['server_host']}:{cfg['server_port']} on Minecraft {cfg['mc_version']}
{edge_line}
Server ID: {cfg['server_id']}
"""
