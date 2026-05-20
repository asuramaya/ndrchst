"""Self-update for the pilot.

On launch the pilot checks a small manifest hosted at the downloads base
— a Cloudflare R2 bucket fronted by a custom domain (global CDN, off the
operator's residential uplink, scales to any number of clients). If a
newer build exists for this OS/arch it downloads + verifies it and swaps
the running binary in place.

Manifest layout (``<base>/latest.json``)::

    {
      "version": "0.2.0",
      "notes": "what changed",
      "assets": {
        "linux-x86_64":   {"file": "ndrchst-pilot-linux-x86_64",       "sha256": "…"},
        "windows-x86_64": {"file": "ndrchst-pilot-windows-x86_64.exe",  "sha256": "…"},
        "macos-arm64":    {"file": "ndrchst-pilot-macos-arm64",         "sha256": "…"}
      }
    }

Each asset is downloaded from ``<base>/<file>``. Updates only apply to a
frozen (PyInstaller) build — in a dev checkout we just report that one is
available. The check is cheap + best-effort; any failure is swallowed so
a flaky network never blocks launch.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import __version__

_UA = "ndrchst-pilot-updater"


@dataclass(frozen=True, slots=True)
class UpdateInfo:
    version: str
    url: str
    sha256: str | None
    notes: str


def is_frozen() -> bool:
    """True when running as a PyInstaller binary (where self-replace is
    meaningful). A dev checkout reports updates but doesn't swap itself."""
    return bool(getattr(sys, "frozen", False))


def platform_key() -> str:
    """Match the CI artifact naming in .github/workflows/build-pilot.yml."""
    sysname = platform.system()
    machine = platform.machine().lower()
    if sysname == "Windows":
        return "windows-x86_64"
    if sysname == "Darwin":
        return "macos-arm64" if machine in ("arm64", "aarch64") else "macos-x86_64"
    # Linux / other
    arch = "x86_64" if machine in ("x86_64", "amd64") else machine
    return f"linux-{arch}"


def _ver_tuple(v: str) -> tuple[int, ...]:
    """Parse a dotted version into a comparable int tuple. Non-numeric
    suffixes (e.g. '-rc1') are dropped — good enough for ordering builds."""
    parts: list[int] = []
    for chunk in v.strip().lstrip("v").split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    return tuple(parts)


def _is_newer(remote: str, local: str) -> bool:
    return _ver_tuple(remote) > _ver_tuple(local)


def check(base_url: str, *, current: str = __version__) -> UpdateInfo | None:
    """Return UpdateInfo if the manifest advertises a newer build with an
    asset for this platform; otherwise None. Never raises."""
    if not base_url:
        return None
    manifest_url = base_url.rstrip("/") + "/latest.json"
    try:
        req = urllib.request.Request(manifest_url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    remote_ver = str(data.get("version") or "")
    if not remote_ver or not _is_newer(remote_ver, current):
        return None
    asset = (data.get("assets") or {}).get(platform_key())
    if not asset or not asset.get("file"):
        return None
    return UpdateInfo(
        version=remote_ver,
        url=base_url.rstrip("/") + "/" + asset["file"],
        sha256=asset.get("sha256"),
        notes=str(data.get("notes") or ""),
    )


def _download_verified(url: str, sha256: str | None, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    h = hashlib.sha256()
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(req, timeout=120) as resp, tmp.open("wb") as f:
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
    if sha256 and h.hexdigest().lower() != sha256.lower():
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"checksum mismatch (expected {sha256[:12]}…, got {h.hexdigest()[:12]}…)"
        )
    tmp.replace(dest)


def self_update(info: UpdateInfo, *, on_log: Callable[[str], None]) -> bool:
    """Download the new build, verify it, and swap the running binary.

    Returns True if the swap was initiated (the caller should exit / the
    process re-execs into the new build). Returns False if we couldn't or
    shouldn't apply (dev checkout, download failure) — caller keeps going
    on the current version."""
    if not is_frozen():
        on_log(f"Update {info.version} available (dev checkout — not self-updating)")
        return False

    exe = Path(sys.executable).resolve()
    staged = exe.parent / f".{exe.name}.new"
    on_log(f"Downloading pilot {info.version}…")
    try:
        _download_verified(info.url, info.sha256, staged)
    except Exception as e:
        on_log(f"Update download failed: {e}")
        staged.unlink(missing_ok=True)
        return False

    on_log(f"Applying update {info.version} and restarting…")
    try:
        if os.name == "nt":
            _apply_windows(exe, staged)
        else:
            _apply_posix(exe, staged)
    except Exception as e:
        on_log(f"Update apply failed: {e}")
        staged.unlink(missing_ok=True)
        return False
    return True


def _apply_posix(exe: Path, staged: Path) -> None:
    """POSIX can replace a running binary's path (the live process keeps
    the old inode). Swap it in, mark executable, and re-exec the new one."""
    os.replace(staged, exe)
    exe.chmod(0o755)
    os.execv(str(exe), [str(exe), *sys.argv[1:]])  # never returns


def _apply_windows(exe: Path, staged: Path) -> None:
    """Windows can't overwrite a running .exe. Spawn a detached helper
    that waits for this process to exit, swaps the file, and relaunches."""
    helper = exe.parent / "_ndrchst_update.cmd"
    script = (
        "@echo off\r\n"
        ":wait\r\n"
        "timeout /t 1 /nobreak >nul\r\n"
        f'del "{exe}" >nul 2>&1\r\n'
        f'if exist "{exe}" goto wait\r\n'
        f'move /y "{staged}" "{exe}" >nul\r\n'
        f'start "" "{exe}"\r\n'
        'del "%~f0"\r\n'
    )
    helper.write_text(script, encoding="ascii")
    subprocess.Popen(
        ["cmd", "/c", str(helper)],
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NO_WINDOW", 0),
        close_fds=True,
    )
    sys.exit(0)
