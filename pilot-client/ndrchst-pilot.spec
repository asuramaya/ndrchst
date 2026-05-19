# PyInstaller spec — single-file ndrchst pilot launcher.
# Builds on whichever OS it runs (Linux/macOS/Windows); PyInstaller does
# not cross-compile, so CI runs this on a matrix of OS runners.
#
#   pyinstaller ndrchst-pilot.spec
#
# cloudflared is NOT bundled — the pilot fetches the right per-OS binary
# at first run (see ndrchst_pilot/tunnel.py). portablemc + its loader
# addons (forge/neoforge live in portablemc.forge) are pure Python and
# get collected here.
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

hiddenimports = collect_submodules("portablemc")

a = Analysis(
    ["run_pilot.py"],
    pathex=["src"],
    binaries=[],
    datas=[],
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
    name="ndrchst-pilot",
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
