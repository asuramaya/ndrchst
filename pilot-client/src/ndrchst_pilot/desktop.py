"""Desktop integration — make the pilot a real installed app.

A downloaded binary on its own gives the player nothing to click. This
module registers an application entry / shortcut for the current OS so
the pilot shows up in the launcher / Start menu and (where the desktop
supports it) on the desktop:

  - Linux:   ~/.local/share/applications/<slug>.desktop  (+ ~/Desktop)
  - Windows: Start Menu + Desktop .lnk (via PowerShell, no extra deps)
  - macOS:   symlink the .app into ~/Applications (best-effort)

`ensure_installed_once()` runs this idempotently on first frozen launch
(guarded by a marker file) so the player never has to think about it.
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _exec_command() -> str:
    """The command a shortcut should run. Frozen → the binary itself;
    dev checkout → `python -m ndrchst_pilot` (so shortcuts work in dev too)."""
    exe = sys.executable
    if is_frozen():
        return f'"{exe}"'
    return f'"{exe}" -m ndrchst_pilot'


def _bundled_icon() -> Path | None:
    """Icon shipped alongside the binary, if PyInstaller bundled one."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    for name in ("icon.png", "assets/icon.png"):
        p = base / name
        if p.exists():
            return p
    return None


def _slug(app_name: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in app_name.lower()).strip("-") or "ndrchst-pilot"


def install_shortcut(
    *,
    app_name: str = "ndrchst Pilot",
    exec_command: str | None = None,
    on_log: Callable[[str], None] = lambda _m: None,
    apps_dir: Path | None = None,
    desktop_dir: Path | None = None,
) -> bool:
    """Create an application entry / shortcut for the current OS.

    Returns True on success. Best-effort — failures are logged, never
    raised, so a launch is never blocked by shortcut creation. The
    *_dir overrides exist for testing."""
    cmd = exec_command or _exec_command()
    system = platform.system()
    try:
        if system == "Linux":
            return _install_linux(app_name, cmd, on_log, apps_dir, desktop_dir)
        if system == "Windows":
            return _install_windows(app_name, on_log)
        if system == "Darwin":
            return _install_macos(app_name, on_log, apps_dir)
    except Exception as e:
        on_log(f"Couldn't create shortcut: {e}")
        return False
    on_log(f"Shortcut creation not supported on {system}")
    return False


def _install_linux(
    app_name: str,
    cmd: str,
    on_log: Callable[[str], None],
    apps_dir: Path | None,
    desktop_dir: Path | None,
) -> bool:
    slug = _slug(app_name)
    apps = apps_dir or (Path.home() / ".local" / "share" / "applications")
    apps.mkdir(parents=True, exist_ok=True)

    icon = _bundled_icon()
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={app_name}",
        "Comment=Play on ndrchst",
        f"Exec={cmd}",
        "Terminal=false",
        "Categories=Game;",
        "StartupNotify=true",
    ]
    if icon is not None:
        lines.append(f"Icon={icon}")
    entry = "\n".join(lines) + "\n"

    target = apps / f"{slug}.desktop"
    target.write_text(entry)
    target.chmod(0o755)
    on_log(f"Installed application entry: {target}")

    # Also drop one on the Desktop if the dir exists, and mark it trusted
    # (GNOME/Nautilus refuses to run untrusted .desktop launchers).
    desk = desktop_dir if desktop_dir is not None else (Path.home() / "Desktop")
    if desk.exists():
        dtarget = desk / f"{slug}.desktop"
        dtarget.write_text(entry)
        dtarget.chmod(0o755)
        if _which("gio"):
            subprocess.run(
                ["gio", "set", str(dtarget), "metadata::trusted", "true"],
                check=False, capture_output=True,
            )
        on_log(f"Added desktop shortcut: {dtarget}")
    return True


def _install_windows(app_name: str, on_log: Callable[[str], None]) -> bool:
    slug = _slug(app_name)
    exe = sys.executable
    appdata = os.environ.get("APPDATA", "")
    start_menu = (
        Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        if appdata else None
    )
    desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    made = []
    for folder in (start_menu, desktop):
        if folder is None:
            continue
        folder.mkdir(parents=True, exist_ok=True)
        lnk = folder / f"{app_name}.lnk"
        ps = (
            "$s=(New-Object -ComObject WScript.Shell).CreateShortcut("
            f"'{lnk}');$s.TargetPath='{exe}';"
            f"$s.WorkingDirectory='{Path(exe).parent}';$s.Save()"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            check=True, capture_output=True,
        )
        made.append(str(lnk))
    if made:
        on_log("Created shortcuts: " + ", ".join(made))
        return True
    return False


def _install_macos(app_name: str, on_log: Callable[[str], None], apps_dir: Path | None) -> bool:
    # A PyInstaller windowed build is a .app bundle; sys.executable points
    # inside it at Contents/MacOS/<bin>. Find the .app root and link it
    # into ~/Applications so it shows up in Launchpad / Spotlight.
    exe = Path(sys.executable)
    app_root = None
    for parent in exe.parents:
        if parent.suffix == ".app":
            app_root = parent
            break
    if app_root is None:
        on_log("Not running from a .app bundle — drag the app to Applications manually")
        return False
    apps = apps_dir or (Path.home() / "Applications")
    apps.mkdir(parents=True, exist_ok=True)
    link = apps / app_root.name
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(app_root)
    on_log(f"Linked {app_root.name} into {apps}")
    return True


def _which(name: str) -> str | None:
    import shutil
    return shutil.which(name)


def ensure_installed_once(
    *,
    app_name: str,
    data_dir: Path,
    on_log: Callable[[str], None] = lambda _m: None,
) -> None:
    """Install the shortcut on first frozen launch only. A marker file in
    the data dir makes this a one-time, idempotent action."""
    if not is_frozen():
        return
    marker = data_dir / ".shortcut-installed"
    if marker.exists():
        return
    data_dir.mkdir(parents=True, exist_ok=True)
    if install_shortcut(app_name=app_name, on_log=on_log):
        marker.write_text(_exec_command())
