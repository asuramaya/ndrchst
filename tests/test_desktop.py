"""Desktop-integration tests for the client launcher (shortcuts + icon).

The launcher is a separate package (client/src/ndrchst_client); desktop.py is
pure stdlib, so we add it to the path and exercise the Linux .desktop writer
with temp dirs — no display, no frozen binary needed.
"""
from __future__ import annotations

import platform
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "client" / "src"))

from ndrchst_client import desktop


@pytest.mark.skipif(platform.system() != "Linux", reason="Linux .desktop entry")
def test_install_shortcut_writes_entry_with_icon_and_wmclass(tmp_path: Path):
    apps = tmp_path / "apps"
    desk = tmp_path / "Desktop"
    desk.mkdir()
    ok = desktop.install_shortcut(
        app_name="ndrchst Client",
        exec_command='"/opt/ndrchst-client" ',
        apps_dir=apps, desktop_dir=desk,
    )
    assert ok
    entry = (apps / "ndrchst-client.desktop").read_text()
    assert "Type=Application" in entry
    assert 'Exec="/opt/ndrchst-client"' in entry
    assert "StartupWMClass=ndrchst" in entry          # groups window under the icon
    assert "Categories=Game;" in entry
    assert "Icon=" in entry and "icon.png" in entry    # assets/icon.png resolves
    # also dropped on the Desktop
    assert (desk / "ndrchst-client.desktop").exists()


@pytest.mark.skipif(platform.system() != "Linux", reason="Linux .desktop entry")
def test_desktop_entry_registers_ndrchst_scheme(tmp_path: Path):
    # A temp apps_dir means xdg-mime is skipped (no global mimeapps.list mutation),
    # but the .desktop must still declare the scheme handler + pass the URL through.
    apps = tmp_path / "apps"
    ok = desktop.install_shortcut(
        app_name="ndrchst Client",
        exec_command='"/opt/ndrchst-client"',
        apps_dir=apps, desktop_dir=tmp_path / "no-desktop",
    )
    assert ok
    entry = (apps / "ndrchst-client.desktop").read_text()
    assert "MimeType=x-scheme-handler/ndrchst;" in entry  # declares the handler
    assert "%u" in entry                                  # the URL is passed through


def test_slug_is_filesystem_safe():
    assert desktop._slug("ndrchst Client") == "ndrchst-client"
    assert desktop._slug("!!!") == "ndrchst-client"  # falls back, never empty
