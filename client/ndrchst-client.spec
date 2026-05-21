# PyInstaller spec — single-file ndrchst client launcher.
# Builds on whichever OS it runs (Linux/macOS/Windows); PyInstaller does
# not cross-compile, so CI runs this on a matrix of OS runners.
#
#   pyinstaller ndrchst-client.spec
#
# cloudflared is NOT bundled — the client fetches the right per-OS binary
# at first run (see ndrchst_client/tunnel.py). portablemc + its loader
# addons (forge/neoforge live in portablemc.forge) are pure Python and
# get collected here.
import os

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

hiddenimports = collect_submodules("portablemc")

# Bundle the themed UI assets (window/taskbar icon, brand glyph, banner GIF) INTO
# the binary so a standalone exe is self-contained — the desktop-shortcut icon
# and window icon both resolve from here at runtime (see app.py / desktop.py).
# The assets dir is gitignored (rebuilt by scripts/build_game_assets.py), so it
# may be absent in a clean CI checkout; build without an icon rather than fail.
# Run the asset generator before `pyinstaller` to ship the themed icon.
_assets_dir = os.path.join("src", "ndrchst_client", "assets")
datas = [
    (os.path.join(_assets_dir, name), "ndrchst_client/assets")
    for name in (os.listdir(_assets_dir) if os.path.isdir(_assets_dir) else [])
    if os.path.isfile(os.path.join(_assets_dir, name))
]

a = Analysis(
    ["run_client.py"],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="ndrchst-client",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    # Windowed (no console) on macOS/Windows; on Linux a console is
    # harmless and aids debugging. PyInstaller maps console=False to
    # --windowed only where it matters.
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
